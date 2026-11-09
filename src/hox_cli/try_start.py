"""Start a test build's real processes and see whether they come up.

Runs on the node, as slapsoft, on what /srv/hox-dev/run-build.sh built: stdlib only,
for the node's Python, sent over ssh by `hox try-start`. It reads the instance profile
rendered by the build and runs its own process scripts, with its environment, behind
its haproxy and with a PostgreSQL of its own. What needs the master is replaced: no
frontend, no instance parameters, no monitoring, and supervisord's restarts are a
failure here. Everything lives in a temporary directory, removed at the end.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

BUILDS = Path("/srv/hox-dev")
# ponytail: fixed ports, so one run at a time; no node has this partition.
PARTITION = "slappart99"
# Its own loopback address and the stack's port: some apps can't be told another.
DATABASE_HOST = "127.0.0.99"
FRONTEND = "try-start.invalid"
REFERENCE = re.compile(r"(?<!\$)\$\{([^:}]*):([^}]*)\}")


def parse(text: str) -> dict[str, dict[str, str]]:
    """A buildout profile's sections, with the indentation of multi-line values."""
    sections: dict[str, dict[str, str]] = {}
    section = option = None
    for line in text.splitlines():
        if header := re.fullmatch(r"\[([^\]]+)\]\s*", line):
            section, option = sections.setdefault(header[1], {}), None
        elif section is None or line.startswith("#"):
            continue
        elif assignment := re.match(r"(<|[\w.+-]+)\s*=\s*(.*)", line):
            option = assignment[1]
            section[option] = assignment[2]
        elif option and (not line.strip() or line[0].isspace()):
            section[option] += "\n" + line
    return {
        name: {k: v.rstrip() for k, v in options.items()}
        for name, options in sections.items()
    }


class Options:
    """What a recipe's Python gets as `options`."""

    def __init__(self, profile: Profile, section: str):
        self.profile, self.section = profile, section

    def __getitem__(self, option: str) -> str:
        return self.profile.get(self.section, option)

    def __setitem__(self, option: str, value: str) -> None:
        self.profile.sections[self.section][option] = value


class Profile:
    """A rendered profile's values, computed when asked for, as buildout's are."""

    def __init__(self, text: str, overrides: dict[tuple[str, str], str]):
        self.sections = parse(text)
        self.overrides = overrides
        self.written: set[str] = set()
        self.buildout = {"slap-connection": {"partition-id": PARTITION}}

    def get(self, section: str, option: str) -> str:
        if (section, option) in self.overrides:
            return self.overrides[section, option]
        options = self.sections.get(section, {})
        if option not in options and "init" in options:
            exec(
                textwrap.dedent(options["init"]),
                {"options": Options(self, section), "self": self},
            )  # ken: S102 intentional, the profile's own recipe code, as buildout runs it
            options.pop("init")
        if (
            option not in options
            and options.get("recipe") == "slapos.cookbook:generate.password"
        ):
            options[option] = secrets.token_urlsafe(24)
        if option not in options and "<" in options:
            return self.get(options["<"], option)
        if option not in options:
            raise KeyError(f"${{{section}:{option}}}: not in the profile")
        value = self.resolve(section, options[option])
        if option == "output" and "inline" in options and section not in self.written:
            self.written.add(section)
            self.write(section, value, options)
        return value

    def resolve(self, section: str, value: str) -> str:
        value = REFERENCE.sub(lambda m: self.get(m[1] or section, m[2]), value)
        return value.replace("$${", "${")

    def write(self, section: str, path: str, options: dict[str, str]) -> None:
        inline = (
            "" if "context" in options else self.resolve(section, options["inline"])
        )  # no parameters
        text = textwrap.dedent(inline).strip().replace('{{ "\\n" }}', "") + "\n"
        Path(path).write_text(text)

    def environment(self) -> dict[str, str]:
        lines = (
            textwrap
            .dedent(self.get("process-base", "environment"))
            .strip()
            .splitlines()
        )
        return dict(line.split("=", 1) for line in lines)


class Run:
    def __init__(self, build: Path, home: Path):
        self.home = home
        self.profile = Profile(
            (build / "instance.cfg").read_text(),
            {
                ("buildout", "directory"): str(home),
                ("slap-configuration", "ipv4-random"): DATABASE_HOST,
                ("slap-configuration", "ipv6-random"): "::1",
                ("request-frontend", "connection-secure_access"): f"https://{FRONTEND}",
                ("request-frontend", "connection-domain"): FRONTEND,
                ("release-key", "marker"): str(home / "var/run/release-try-start"),
            },
        )
        self.env: dict[str, str] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.postgres: list[str] | None = None

    def log(self, name: str) -> Path:
        return self.home / f"{name}.log"

    def start(self, name: str, command: str) -> None:
        with self.log(name).open("w") as log:
            self.processes[name] = subprocess.Popen(
                shlex.split(command),
                env=self.env,
                cwd=self.home,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def prepare(self) -> None:
        sections = self.profile.sections
        for option in sections["directory"]:
            if option != "recipe":
                Path(self.profile.get("directory", option)).mkdir(
                    parents=True, exist_ok=True
                )
        if "workdir" in sections:
            location = self.profile.get("workdir", "location")
            code = textwrap.dedent(sections["workdir"]["install"])
            exec(
                code,
                {"options": Options(self.profile, "workdir"), "location": location},
            )  # ken: S102 intentional, as above
        self.env = self.profile.environment()
        if "postgresql" in sections:
            self.start_postgres()
        if "mariadb" in sections:
            self.start("mariadb", self.profile.get("mariadb", "command-line"))
        if "haproxy" in sections:
            self.start("haproxy", self.profile.get("haproxy", "command-line"))

    def start_postgres(self) -> None:
        def get(option: str) -> str:
            return self.profile.get("postgresql", option)

        def tool(program: str) -> str:
            return str(Path(get("bin")) / program)

        pgdata, user, name, port = (
            get("pgdata-directory"),
            get("superuser"),
            get("dbname"),
            get("port"),
        )
        password = self.home / "postgres-password"
        password.write_text(get("password"))
        subprocess.run(
            [
                tool("initdb"),
                "-D",
                pgdata,
                "-U",
                user,
                "-A",
                "md5",
                f"--pwfile={password}",
                "-E",
                "UTF8",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        options = f"-p {port} -k {self.home} -c listen_addresses={DATABASE_HOST}"
        self.postgres = [tool("pg_ctl"), "-D", pgdata, "-m", "fast", "stop"]
        subprocess.run(
            [
                tool("pg_ctl"),
                "-D",
                pgdata,
                "-w",
                "-l",
                str(self.log("postgres")),
                "-o",
                options,
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [tool("createdb"), "-h", DATABASE_HOST, "-p", port, "-U", user, name],
            check=True,
            env={**os.environ, "PGPASSWORD": get("password")},
        )

    def url(self) -> str:
        if "backend-health-promise" in self.profile.sections:
            return self.profile.get("backend-health-promise", "config-url")
        return self.profile.get("app-parameters", "url")

    def status(self, url: str) -> int | str:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        try:
            return urllib.request.build_opener(NoRedirect).open(url, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
        except OSError as e:
            return str(e)

    def wait(self, timeout: int) -> str | None:
        """Why it failed, or None when the app answers and all that runs once has run."""
        url, last = self.url(), None
        print(f"waiting for {url}", flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            done = {name: p.poll() for name, p in self.processes.items()}
            for name, code in done.items():
                if code not in (None, 0) or (
                    code == 0 and name not in ("release", "after-start")
                ):
                    return f"{name} exited with {code}"
            last = self.status(url)
            answers = isinstance(last, int) and last < 400
            if (
                answers
                and done.get("release", 0) == 0
                and done.get("after-start", 0) == 0
            ):
                print(f"ok    {url}: {last}")
                return None
            time.sleep(2)
        pending = [
            name
            for name in ("release", "after-start")
            if self.processes.get(name) and done[name] is None
        ]
        return f"after {timeout} s: {url} says {last}" + (
            f", still running: {', '.join(pending)}" if pending else ""
        )

    def stop(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in self.processes.values():
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        if self.postgres:
            subprocess.run(self.postgres, stdout=subprocess.DEVNULL, check=False)

    def run(self, timeout: int) -> int:
        self.prepare()
        names = [
            s[len("process-") : -len("-script")]
            for s in self.profile.sections
            if s.startswith("process-") and s.endswith("-script")
        ]
        print("processes:", " ".join(names), flush=True)
        for name in names:
            self.start(name, self.profile.get(f"process-{name}", "command-line"))
        failure = self.wait(timeout)
        if failure is None:
            return 0
        print(f"FAILED: {failure}")
        for name in self.processes:
            print(f"--- {name}")
            print(
                "\n".join(self.log(name).read_text(errors="replace").splitlines()[-25:])
            )
        return 1


def main(software: str, timeout: str) -> int:
    home = Path(tempfile.mkdtemp(prefix=f"try-{software}-", dir=BUILDS))
    run = Run(BUILDS / software, home)
    try:
        return run.run(int(timeout))
    finally:
        run.stop()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
