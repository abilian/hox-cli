"""From a Hop3 recipe (a catalog app's hop3.toml and its files) to a SlapOS
software release on the hop3 stack of slapos_abilian_edge (stack/hop3/)."""

import hashlib
import re
import shlex
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import Requirement

from .lock import Pin, environment, node_tags, render_versions, wheel_kind


class PortError(Exception):
    pass


# The catalog's presentation layer and Hop3's own checks: not part of the recipe.
OVERLAY = re.compile(r"catalog\.toml|readme(-\w+)?\.md|icon\.\w+|check\.py|screenshots/.*")

# What the hop3 stack does without a word from the recipe, or what doesn't apply
# on SlapOS; anything else the converter doesn't map is reported.
IGNORED = {"metadata", "test", "port", "domains", "deploy", "backup", "limits", "contexts"}

PIP_INSTALL = "pip install -r requirements.txt"


@dataclass
class Release:
    name: str
    software_cfg: str
    versions_cfg: str
    files: list[str]  # the recipe's, relative to its directory
    notes: list[str] = field(default_factory=list)  # what wasn't mapped, or needs a look


# Fetches a package's release files from PyPI: name, version -> file names.
PyPI = Callable[[str, str], list[str]]


def pypi_files(name: str, version: str) -> list[str]:
    import httpx2

    response = httpx2.get(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30)
    if response.status_code == 404:
        raise PortError(f"{name} {version} is not on PyPI")
    response.raise_for_status()
    return [f["filename"] for f in response.json()["urls"]]


def port(recipe: Path, python: str, pypi: PyPI = pypi_files) -> Release:
    """The release for the recipe in `recipe` (the directory of its hop3.toml)."""
    spec = tomllib.loads((recipe / "hop3.toml").read_text())
    name = spec["metadata"]["id"]
    build, run = spec.get("build", {}), spec.get("run", {})
    notes: list[str] = []
    if build.get("builder", "local") != "local":
        raise PortError(f"builder {build['builder']!r}: only the local builder is supported")
    if build.get("toolchain") != "python":
        raise PortError(f"toolchain {build.get('toolchain')!r}: only python is supported so far")

    commands = _commands(build.get("before-build"))
    install = _commands(build.get("build"))
    requirements = recipe / "requirements.txt"
    if install == [PIP_INSTALL] or (not install and requirements.exists()):
        install = []
    elif install:
        raise PortError(f"[build] build = {install}: only {PIP_INSTALL!r} is supported so far")
    else:
        raise PortError("no requirements.txt: apps installed from their source aren't supported so far")
    commands += _commands(build.get("after-build"))
    pins, eggs = _requirements(requirements, python, pypi)

    environment = ["HOST_NAME=$DOMAIN", "HOP3_PUBLIC_URL=$URL"]
    secrets = []
    for key, value in spec.get("env", {}).items():
        if key == "computed":
            environment += [f"{k}={_shell(_variables(v))}" for k, v in value.items()]
        elif key.startswith("_"):
            continue
        elif isinstance(value, str):
            environment.append(f"{key}={_shell(value)}")
        elif "generate" in value:
            secrets.append(key)
            notes.append(f"[env] {key}: generated as a 32-character password, not {value['generate']!r}")
        elif value.get("key") in ("domain", "hostname") and "from" not in value:
            environment.append(f"{key}=$DOMAIN")
        elif value.get("key") == "name" and "from" not in value:
            environment.append(f"{key}={name}")
        else:
            notes.append(f"[env] {key} = {value}: not mapped")
    processes = {}
    release = _commands(run.get("before-run"))
    if admin := spec.get("admin"):
        secrets.append("HOP3_ADMIN_PASSWORD")
        if "username" in admin:
            environment.append(f"HOP3_ADMIN_USER={_shell(admin['username'])}")
        if admin.get("email") == "operator":
            notes.append("[admin] email: set HOP3_ADMIN_EMAIL as an instance parameter")
        elif "email" in admin:
            environment.append(f"HOP3_ADMIN_EMAIL={_shell(admin['email'])}")
        release += _commands(admin.get("create"))
    if release:
        processes["release"] = " && ".join(release)
    if "start" in run:
        processes["web"] = " && ".join(_commands(run["start"]))
    processes |= {k: " && ".join(_commands(v)) for k, v in run.get("workers", {}).items()}
    for process, command in processes.items():
        if "\n" in command or "${" in command:
            raise PortError(f"{process}: {command!r}: commands must be one line, without ${{…}}")
    if "probe" in spec:
        notes.append("[probe]: not created, nothing on SlapOS signs in with it")

    volumes = []
    for volume in spec.get("volumes", []):
        if volume.get("type", "persist") == "persist":
            volumes.append(f"{volume['name']} {volume['target']}")
        # A scratch volume is reset by every deploy: the copy of the build is.
    addons = [a["type"] for a in spec.get("addons", [])]
    database = "postgres" in addons
    notes += [f"[[addons]] {a}: not supported" for a in addons if a != "postgres"]
    notes += [f"[{s}]: not mapped" for s in spec if s not in IGNORED | {
        "build", "run", "env", "admin", "probe", "volumes", "addons", "healthcheck"}]
    notes += [f"[{s}] packages = {spec[s]['packages']}: needs SlapOS components"
              for s in ("build", "run") if spec.get(s, {}).get("packages")]

    files = sorted(f for f in (str(p.relative_to(recipe)) for p in recipe.rglob("*") if p.is_file())
                   if not OVERLAY.fullmatch(f))
    version = spec["metadata"].get("version", "")
    lines = [
        f"# {spec['metadata'].get('title', name)} {version}, from its Hop3 recipe in hop3/,",
        "# by `hox port`: change the recipe and port it again rather than edit this file.",
        "[buildout]",
        "extends =",
        *(["  ../../component/postgresql/buildout.cfg"] if database else []),
        "  ../../stack/hop3/buildout.cfg",
        "  ./versions.cfg",
        "",
        "[python3]",
        f"<= python{python}",
        "",
        "[hop3]",
        "base = ${:_profile_base_location_}/hop3",
        "files =",
        *(f"  {f} {hashlib.md5((recipe / f).read_bytes()).hexdigest()}" for f in files),
        "commands =",
        *(f"  {c}" for c in commands),
        "",
        "[python-app]",
        f"name = {name}",
        "eggs =",
        *(f"  {e}" for e in eggs),
        *([f"health = {spec['healthcheck']['path']}"] if "path" in spec.get("healthcheck", {}) else []),
        *(["postgresql-bin = ${postgresql:location}/bin"] if database else []),
        *([f"secrets = {' '.join(secrets)}"] if secrets else []),
        "environment =",
        *(f"  {e}" for e in environment),
        *(["volumes =", *(f"  {v}" for v in volumes)] if volumes else []),
        "",
        "[python-app-processes]",
        *(f"{k} = {v}" for k, v in processes.items()),
        "",
    ]
    return Release(name, "\n".join(lines), render_versions(pins, "hop3/requirements.txt by `hox port`", python),
                   files, notes)


def write(release: Release, recipe: Path, out: Path) -> None:
    """Write the release in `out` (software/<name>/), the recipe's files in out/hop3/."""
    if (out / "hop3").exists():
        shutil.rmtree(out / "hop3")
    for f in release.files:
        (out / "hop3" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(recipe / f, out / "hop3" / f)
    (out / "software.cfg").write_text(release.software_cfg)
    (out / "versions.cfg").write_text(release.versions_cfg)


def _commands(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [c.strip() for c in ([value] if isinstance(value, str) else value) if c.strip()]


def _requirements(path: Path, python: str, pypi: PyPI) -> tuple[list[Pin], list[str]]:
    """The pins of a compiled requirements.txt, and the eggs to install (all of them,
    as `pip install -r` does)."""
    env = environment(python)
    supported = node_tags(python)
    pins, eggs = [], []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            raise PortError(f"{path.name}: {line!r}: only pinned requirements are supported")
        req = Requirement(line)
        if req.marker and not req.marker.evaluate(env):
            continue
        version = next((s.version for s in req.specifier if s.operator == "=="), None)
        if version is None:
            raise PortError(f"{path.name}: {line!r} isn't pinned")
        files = pypi(req.name, version)
        pins.append(Pin(req.name, version, any(f.endswith((".tar.gz", ".zip")) for f in files),
                        wheel_kind([f for f in files if f.endswith(".whl")], supported)))
        eggs.append(f"{req.name}[{','.join(sorted(req.extras))}]" if req.extras else req.name)
    return sorted(pins, key=lambda p: p.name.lower()), eggs


def _variables(value: str) -> str:
    """${NAME} as $NAME, which buildout leaves alone, where the shell reads the same."""
    converted = re.sub(r"\$\{(\w+)\}(?!\w)", r"$\1", value)
    if "${" in converted:
        raise PortError(f"{value!r}: a ${{…}} followed by a name character can't be written for buildout")
    return converted


def _shell(value: str) -> str:
    """A value for a shell assignment, $NAME expanded."""
    if "$" not in value:
        return shlex.quote(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`") + '"'
