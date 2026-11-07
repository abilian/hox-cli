"""From a Hop3 recipe (a catalog app's hop3.toml and its files) to a SlapOS
software release on the hop3 stack of slapos_abilian_edge (stack/hop3/).

port() maps the recipe to a Release, a section of hop3.toml at a time;
software_cfg(), versions_cfg() and write() turn the Release into files."""

import hashlib
import re
import shlex
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .lock import (
    BUILDOUT_TOOLS,
    Pin,
    environment,
    node_tags,
    render_versions,
    wheel_kind,
)


class PortError(Exception):
    pass


# The catalog's presentation layer and Hop3's own checks: not part of the recipe.
OVERLAY = re.compile(r"catalog\.toml|readme(-\w+)?\.md|icon\.\w+|check\.py|screenshots/.*")

# What the hop3 stack does without a word from the recipe, or what doesn't apply
# on SlapOS; any other section without a mapper (SECTIONS) is reported.
IGNORED = {"metadata", "test", "port", "domains", "deploy", "backup", "limits", "contexts"}

PIP_INSTALL = "pip install -r requirements.txt"

# The build commands that need Node (SBE's version, already in the node's shared parts).
NODE_COMMAND = re.compile(r"(\w+=\S*\s+)*(npm|npx|node)\b")
NODE_VERSION = "24.11.0"
# SlapOS's Go components, oldest first: an app gets the first that its go.mod allows.
GO_VERSIONS = ("1.23", "1.24", "1.26")
GO_MOD_VERSION = re.compile(r"^go\s+(\d+\.\d+)", re.MULTILINE)
# Packages that buildout can't build by name alone, and the SlapOS component that
# has what their build needs.
COMPONENTS = {"misaka": "python-misaka"}
# In a venv, pip builds them by name: the SlapOS component with the libraries an sdist needs.
BUILD_LIBRARIES = {"cffi": "libffi"}
# The kinds of [env] secret that the stack encodes from 32 random bytes, as Hop3 does
# by default (`secrets` in the stack: NAME:kind). A password is the stack's own kind.
ENCODED_SECRETS = {"hex", "base64", "urlsafe"}
ADMIN_SCRIPT = "hop3-admin-create.sh"

# Fetches a package's release files from PyPI: name, version -> file names.
PyPI = Callable[[str, str], list[str]]

# Pins a project's dependencies for the node: its pyproject.toml, Python -> requirements.
Resolve = Callable[[Path, str], str]

# The pins as a requirements file with their hashes: pins, Python -> its text.
Hashes = Callable[[list[Pin], str], str]


@dataclass
class Release:
    """What the release's software.cfg and versions.cfg say, before they are written."""

    name: str
    title: str
    version: str
    python: str
    recipe: Path  # the recipe's directory
    files: dict[str, str]  # the recipe's files, relative to it: their md5
    generated: dict[str, str] = field(default_factory=dict)  # what the port adds to them: their text
    commands: list[str] = field(default_factory=list)  # run in the build
    toolchain: str = ""
    node: bool = False  # the commands need Node
    go: str = ""  # the app is built with this Go component
    prebuilt: bool = False  # the recipe fetches a binary: provisional, SlapOS builds from source
    egg: str | None = None  # the app's distribution, when installed from its source
    pins: list[Pin] = field(default_factory=list)
    eggs: list[str] = field(default_factory=list)
    venv: bool = False  # installed by pip in a venv of its own, not as eggs by buildout
    requirements: str = ""  # for the venv: the pins with their hashes
    uploaded_prior_to: str = ""  # for the venv: what its packages' builds may fetch
    # What Hop3 gives every app, then what the recipe asks for.
    environment: list[str] = field(default_factory=lambda: ["HOST_NAME=$DOMAIN", "HOP3_PUBLIC_URL=$URL"])
    secrets: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)  # "name target"
    processes: dict[str, list[str]] = field(default_factory=dict)  # name: its commands
    health: str | None = None
    database: bool = False
    mariadb: bool = False
    notes: list[str] = field(default_factory=list)  # what wasn't mapped, or needs a look


def pypi_files(name: str, version: str) -> list[str]:
    import httpx2

    response = httpx2.get(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30)
    if response.status_code == 404:
        raise PortError(f"{name} {version} is not on PyPI")
    response.raise_for_status()
    return [f["filename"] for f in response.json()["urls"]]


def uv_compile(pyproject: Path, python: str) -> str:
    done = subprocess.run(["uv", "pip", "compile", str(pyproject), "--python-version", python,
                           "--python-platform", "x86_64-unknown-linux-gnu", "--no-header", "--no-annotate", "-q"],
                          capture_output=True, text=True, check=False)
    if done.returncode:
        raise PortError(f"uv pip compile {pyproject}: {done.stderr.strip()}")
    return done.stdout


def uv_hashes(pins: list[Pin], python: str) -> str:
    done = subprocess.run(["uv", "pip", "compile", "-", "--no-deps", "--generate-hashes", "--python-version", python,
                           "--python-platform", "x86_64-unknown-linux-gnu", "--no-header", "--no-annotate", "-q"],
                          input="".join(f"{p.name}=={p.version}\n" for p in pins),
                          capture_output=True, text=True, check=False)
    if done.returncode:
        raise PortError(f"uv pip compile --generate-hashes: {done.stderr.strip()}")
    return done.stdout


def port(recipe: Path, python: str, pypi: PyPI = pypi_files, source: Path | None = None,
         resolve: Resolve = uv_compile, venv: bool = False, hashes: Hashes = uv_hashes) -> Release:
    """The release for the recipe in `recipe` (the directory of its hop3.toml).

    An app without a requirements.txt is installed from its source, which the recipe
    fetches at build time: `source` is an unpacked copy, for its name and dependencies,
    or to tell a Go app (Hop3 finds its go.mod, the recipe doesn't name the toolchain)."""
    spec = tomllib.loads((recipe / "hop3.toml").read_text())
    build = spec.setdefault("build", {})  # its mapper refuses what it can't build
    if "toolchain" not in build and source is not None and (source / "go.mod").exists():
        build["toolchain"] = "go"
    meta = spec["metadata"]
    files = sorted(f for f in (str(p.relative_to(recipe)) for p in recipe.rglob("*") if p.is_file())
                   if not OVERLAY.fullmatch(f))
    release = Release(meta["id"], meta.get("title", meta["id"]), meta.get("version", ""), python, recipe,
                      {f: hashlib.md5((recipe / f).read_bytes()).hexdigest() for f in files})
    for name, mapper in SECTIONS.items():
        if name in spec:
            mapper(release, spec[name])
    release.notes += [f"[{s}]: not mapped" for s in spec if s not in SECTIONS and s not in IGNORED]
    if release.go and source is not None:
        release.go = _go_component((source / "go.mod").read_text())
    if release.toolchain != "python":
        if venv:
            raise PortError(f"{release.name}: --venv is for a Python app")
        return release
    if (recipe / "requirements.txt").exists():
        requirements = (recipe / "requirements.txt").read_text()
    else:
        requirements = _from_source(release, source, resolve)
    release.pins, release.eggs = _requirements(requirements, python, pypi, frozenset() if venv else BUILDOUT_TOOLS)
    if venv:
        release.venv = True
        release.requirements = hashes(release.pins, python)
        release.uploaded_prior_to = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return release


def _from_source(release: Release, source: Path | None, resolve: Resolve) -> str:
    if source is None:
        raise PortError("no requirements.txt: for an app installed from its source, "
                        "give its unpacked release with --source")
    pyproject = source / "pyproject.toml"
    if not pyproject.exists():
        raise PortError(f"{source}: no pyproject.toml")
    release.egg = tomllib.loads(pyproject.read_text())["project"]["name"]
    release.notes.append(f"{release.egg} from its source: its dependencies are pinned as resolved today")
    return resolve(pyproject, release.python)


def _build(release: Release, build: dict) -> None:
    if build.get("builder", "local") != "local":
        raise PortError(f"builder {build['builder']!r}: only the local builder is supported")
    toolchain = build.get("toolchain")
    if toolchain not in ("python", "go", "generic", "node", "php"):
        raise PortError(f"toolchain {toolchain!r}: only python, go, generic, node and php are supported so far "
                        "(a Go recipe that doesn't name it: give its source with --source)")
    release.toolchain = toolchain
    install = _commands(build.get("build"))
    release.go = GO_VERSIONS[0] if toolchain == "go" else ""
    release.prebuilt = toolchain == "generic"
    if toolchain == "python" and install not in ([], [PIP_INSTALL]):
        raise PortError(f"[build] build = {install}: only {PIP_INSTALL!r} is supported so far")
    if release.prebuilt:
        release.notes.append("toolchain 'generic': provisional, runs the binary the recipe fetches, "
                             "until a variant of the recipe builds it from source")
    release.commands = (_commands(build.get("before-build")) + ([] if toolchain == "python" else install)
                        + _commands(build.get("after-build")))
    release.node = toolchain == "node" or any(NODE_COMMAND.match(c) for c in release.commands)
    if toolchain == "php":
        release.processes |= {"fpm": ["hop3-php-fpm"], "web": ["hop3-php-web"]}
        release.environment.append("PHPRC=${hop3-php-ini:output}")
    _packages(release, "build", build)


def _env(release: Release, env: dict) -> None:
    for key, value in env.items():
        if key == "computed":
            release.environment += [f"{k}={_shell(_variables(v))}" for k, v in value.items()]
        elif key.startswith("_"):
            continue
        elif isinstance(value, str):
            release.environment.append(f"{key}={_shell(value)}")
        elif "generate" in value:
            _secret(release, key, value)
        elif value.get("key") in ("domain", "hostname") and "from" not in value:
            release.environment.append(f"{key}=$DOMAIN")
        elif value.get("key") == "name" and "from" not in value:
            release.environment.append(f"{key}={release.name}")
        else:
            release.notes.append(f"[env] {key} = {value}: not mapped")


def _secret(release: Release, key: str, spec: dict) -> None:
    kind = spec["generate"]
    release.secrets.append(f"{key}:{kind}" if kind in ENCODED_SECRETS else key)
    if kind not in ENCODED_SECRETS | {"password"}:
        release.notes.append(f"[env] {key}: generated as a 32-character password, not {kind!r}")
    elif kind != "password" and spec.get("length", 32) != 32:
        release.notes.append(f"[env] {key}: generated from 32 bytes, not {spec['length']}")
    if "prefix" in spec:
        release.notes.append(f"[env] {key}: its prefix {spec['prefix']!r} is left out")


def _run(release: Release, run: dict) -> None:
    release.processes["release"] = _commands(run.get("before-run"))
    if "start" in run:
        release.processes["web"] = _commands(run["start"])
    release.processes |= {k: _commands(v) for k, v in run.get("workers", {}).items()}
    _packages(release, "run", run)


def _admin(release: Release, admin: dict) -> None:
    release.secrets.append("HOP3_ADMIN_PASSWORD")
    if "username" in admin:
        release.environment.append(f"HOP3_ADMIN_USER={_shell(admin['username'])}")
    if admin.get("email") == "operator":  # Hop3's operator: the create scripts require an address
        release.environment.append(f"HOP3_ADMIN_EMAIL=admin@{release.name}.invalid")
        release.notes.append(f"[admin] email: admin@{release.name}.invalid, unless HOP3_ADMIN_EMAIL is an instance parameter")
    elif "email" in admin:
        release.environment.append(f"HOP3_ADMIN_EMAIL={_shell(admin['email'])}")
    create = admin.get("create")
    if not create:  # the app makes its admin from the HOP3_ADMIN_* variables
        return
    if "\n" in create.strip() or "${" in create:  # in a script: buildout would split or substitute it
        release.generated[ADMIN_SCRIPT] = create
        release.files[ADMIN_SCRIPT] = hashlib.md5(create.encode()).hexdigest()
        create = f"bash {ADMIN_SCRIPT}"
    release.processes["after-start"] = _commands(create)


def _probe(release: Release, probe: dict) -> None:
    release.notes.append("[probe]: not created, nothing on SlapOS signs in with it")


def _volumes(release: Release, volumes: list[dict]) -> None:
    # A scratch volume is reset by every deploy, as the copy of the build is.
    release.volumes = [f"{v['name']} {v['target']}" for v in volumes if v.get("type", "persist") == "persist"]


def _addons(release: Release, addons: list[dict]) -> None:
    for addon in addons:
        if addon["type"] == "postgres":
            release.database = True
        elif addon["type"] == "mysql":
            release.mariadb = True
        else:
            release.notes.append(f"[[addons]] {addon['type']}: not supported")


def _proxy(release: Release, proxy: dict) -> None:
    if release.toolchain == "php" and proxy.keys() == {"deny"}:  # for nginx in stack/hop3/php.cfg
        release.environment.append(f"HOP3_PROXY_DENY={_shell(' '.join(proxy['deny']))}")
    else:
        release.notes.append(f"[proxy] {proxy}: not mapped")


def _healthcheck(release: Release, healthcheck: dict) -> None:
    release.health = healthcheck.get("path")


# The sections mapped, in this order: [env.computed] can read what [admin] sets.
SECTIONS: dict[str, Callable[[Release, Any], None]] = {
    "build": _build,
    "run": _run,
    "admin": _admin,
    "env": _env,
    "probe": _probe,
    "volumes": _volumes,
    "addons": _addons,
    "proxy": _proxy,
    "healthcheck": _healthcheck,
}


def software_cfg(release: Release) -> str:
    processes = {k: " && ".join(v) for k, v in release.processes.items() if v}
    lines = [
        f"# {release.title} {release.version}, from its Hop3 recipe in hop3/,",
        "# by `hox port`: change the recipe and port it again rather than edit this file.",
        *(["# Provisional: runs the binary the recipe fetches, where a SlapOS release builds",
           "# from source. To replace with a port of a variant of the recipe that does."] if release.prebuilt else []),
        "[buildout]",
        "extends =",
        *(["  ../../component/golang/buildout.cfg"] if release.go else []),
        *(["  ../../component/nodejs/buildout.cfg"] if release.node else []),
        *(["  ../../component/apache-php/buildout.cfg", "  ../../component/nginx/buildout.cfg"]
          if release.toolchain == "php" else []),
        *(["  ../../component/postgresql/buildout.cfg"] if release.database or release.toolchain == "php" else []),
        *(["  ../../component/mariadb/buildout.cfg"] if release.mariadb else []),
        *(f"  ../../component/{c}/buildout.cfg" for c in _components(release)),
        "  ../../stack/hop3/buildout.cfg",
        *(["  ../../stack/hop3/php.cfg"] if release.toolchain == "php" else []),
        *(["  ../../stack/python-app/venv.cfg", "  ../../stack/hop3/venv.cfg"] if release.venv
          else ["  ./versions.cfg"] if release.pins else []),
        "",
        "[python3]",
        f"<= python{release.python}",
        "",
        *(["[nodejs]", f"<= nodejs-{NODE_VERSION}", ""] if release.node else []),
        *(["[nodejs-headers]", f"<= nodejs-headers-{NODE_VERSION}", "",
           "# Native modules from their source, where npm would fetch a prebuilt binary.",
           "[python-app-git]", "environment +=", "  npm_config_build_from_source=true",
           "  npm_config_tarball=${nodejs-headers:target}", "  NODE_GYP_FORCE_PYTHON=${python3:location}/bin/python3",
           ""] if release.toolchain == "node" else []),
        "[hop3]",
        "base = ${:_profile_base_location_}/hop3",
        "files =",
        *(f"  {f} {md5}" for f, md5 in release.files.items()),
        "commands =",
        *(f"  {c}" for c in release.commands),
        *([f"tools = {_tools(release)}"] if release.go or release.node or release.toolchain == "php" else []),
        "",
        *(["[python-app-requirements]",
           "url = ${:_profile_base_location_}/requirements.txt",
           f"md5sum = {hashlib.md5(release.requirements.encode()).hexdigest()}",
           ""] if release.venv else []),
        "[python-app]",
        f"name = {release.name}",
        *([f"egg = {release.egg}"] if release.egg else []),
        *(_venv_options(release) if release.venv else _eggs(release)),
        *([f"health = {release.health}"] if release.health else []),
        *(["postgresql-bin = ${postgresql:location}/bin"] if release.database else []),
        *(["mariadb-bin = ${mariadb:location}/bin"] if release.mariadb else []),
        *([f"secrets = {' '.join(release.secrets)}"] if release.secrets else []),
        "environment =",
        *(f"  {e}" for e in release.environment),
        *(["volumes =", *(f"  {v}" for v in release.volumes)] if release.volumes else []),
        "",
        "[python-app-processes]",
        *(f"{k} = {v}" for k, v in processes.items()),
        "",
    ]
    return "\n".join(lines)


def versions_cfg(release: Release) -> str:
    source = "the app's pyproject.toml" if release.egg else "hop3/requirements.txt"
    return render_versions(release.pins, f"{source} by `hox port`", release.python)


def write(release: Release, out: Path) -> None:
    """Write the release in `out` (software/<name>/), the recipe's files in out/hop3/."""
    cfg = out / "software.cfg"
    if cfg.exists() and "by `hox port`" not in cfg.read_text():
        raise PortError(f"{cfg} doesn't come from hox port: give the recipe another id")
    if (out / "hop3").exists():
        shutil.rmtree(out / "hop3")
    for f in release.files:
        (out / "hop3" / f).parent.mkdir(parents=True, exist_ok=True)
        if f in release.generated:
            (out / "hop3" / f).write_text(release.generated[f])
        else:
            shutil.copyfile(release.recipe / f, out / "hop3" / f)
    cfg.write_text(software_cfg(release))
    if release.venv:
        (out / "requirements.txt").write_text(release.requirements)
    elif release.pins:
        (out / "versions.cfg").write_text(versions_cfg(release))


def _compiled(release: Release) -> list[str]:
    """What a venv builds from its sdist, where a wheel would bring compiled code."""
    return [canonicalize_name(p.name) for p in release.pins if p.wheel == "binary"]


def _components(release: Release) -> list[str]:
    if release.venv:
        return [BUILD_LIBRARIES[n] for n in _compiled(release) if n in BUILD_LIBRARIES]
    return [COMPONENTS[e] for e in release.eggs if e in COMPONENTS]


def _eggs(release: Release) -> list[str]:
    return ["eggs =",
            *(["  ${python-app-develop:egg}"] if release.egg else []),
            *(f"  ${{{COMPONENTS[e]}:egg}}" if e in COMPONENTS else f"  {e}" for e in release.eggs)]


def _venv_options(release: Release) -> list[str]:
    compiled = _compiled(release)
    return [*([f"no-binary = {' '.join(compiled)}"] if compiled else []),
            *([f"build-libraries = {' '.join(f'${{{c}:location}}' for c in _components(release))}"]
              if _components(release) else []),
            f"uploaded-prior-to = {release.uploaded_prior_to}"]


def _tools(release: Release) -> str:
    """What the build finds on PATH, before what the stack gives every release."""
    tools = [f"${{golang{release.go}:location}}/bin"] if release.go else []
    tools += ["${nodejs:location}/bin"] if release.node else []
    tools += ["${gcc:prefix}/bin", "${binutils:location}/bin"] if release.toolchain == "node" else []
    tools += ["${hop3-php:tools}"] if release.toolchain == "php" else []
    return ":".join([*tools, "${:base-tools}"])


def _go_component(go_mod: str) -> str:
    """The first Go component at least as new as what a go.mod asks for."""
    asked = GO_MOD_VERSION.search(go_mod)
    if asked is None:
        return GO_VERSIONS[0]
    for version in GO_VERSIONS:
        if _minor(version) >= _minor(asked[1]):
            return version
    raise PortError(f"go.mod asks for Go {asked[1]}: SlapOS's newest component here is {GO_VERSIONS[-1]}")


def _minor(version: str) -> int:
    return int(version.split(".")[1])


def _commands(value: str | list[str] | None) -> list[str]:
    """A command or a list of them. Each one line, without ${…}: buildout would
    split the one and substitute the other (write $NAME)."""
    commands = [c.strip() for c in ([] if value is None else [value] if isinstance(value, str) else value)]
    for command in commands:
        if "\n" in command or "${" in command:
            raise PortError(f"{command!r}: commands must be one line, without ${{…}}")
    return [c for c in commands if c]


def _packages(release: Release, section: str, value: dict) -> None:
    if value.get("packages"):
        release.notes.append(f"[{section}] packages = {value['packages']}: needs SlapOS components")


def _requirements(requirements: str, python: str, pypi: PyPI,
                  skip: Collection[str]) -> tuple[list[Pin], list[str]]:
    """The pins of compiled requirements, and the eggs to install (all of them, as
    `pip install -r` does, but those in `skip`: buildout's own, whose versions are SlapOS's)."""
    env = environment(python)
    supported = node_tags(python)
    pins, eggs = [], []
    for line in requirements.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            raise PortError(f"requirements: {line!r}: only pinned requirements are supported")
        req = Requirement(line)
        if (req.marker and not req.marker.evaluate(env)) or canonicalize_name(req.name) in skip:
            continue
        version = next((s.version for s in req.specifier if s.operator == "=="), None)
        if version is None:
            raise PortError(f"requirements: {line!r} isn't pinned")
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
