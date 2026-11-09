"""hox.toml, parsed once into frozen dataclasses."""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "hox.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Master:
    url: str
    cert: Path
    key: Path
    # api.vifib.com serves slap.vifib.com's certificate: verify it under that name.
    api_tls_name: str | None = None


@dataclass(frozen=True)
class Project:
    reference: str


@dataclass(frozen=True)
class Node:
    title: str  # on the master; hox looks its reference up there
    ssh: tuple[str, ...]
    # Where `hox re6st-token` requests the node's re6st token (panel: "Re6st Registries").
    re6st_project: str
    re6st_release: str


@dataclass(frozen=True)
class Check:
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    expect_status: int | None = None
    expect_header: dict[str, str] = field(default_factory=dict)


RAW = "https://raw.githubusercontent.com"


def short_ref(ref: str) -> str:
    """A commit's first nine characters; a branch stays whole."""
    return ref[:9] if re.fullmatch(r"[0-9a-f]{40}", ref) else ref


@dataclass(frozen=True)
class Software:
    name: str
    repo: str
    checkout: Path
    branch: str
    path: str
    software_type: str
    product: str
    instance: str
    checks: tuple[Check, ...] = ()
    # The instance's parameters, sent with every request; None keeps those it has.
    parameters: dict[str, str] | None = None
    # Services of etc/service that exit once their work is done: their exit isn't a failure.
    one_shot: tuple[str, ...] = ()

    def raw_url(self, file: str, ref: str | None = None) -> str:
        return f"{RAW}/{self.repo}/{ref or self.branch}/{file}"

    def pinned_url(self, commit: str) -> str:
        return self.raw_url(self.path, commit)

    @property
    def branch_url(self) -> str:
        return self.raw_url(self.path)

    def ref_of(self, url: str) -> str | None:
        """The branch or commit of one of this software's release URLs; None for another URL."""
        prefix, suffix = f"{RAW}/{self.repo}/", f"/{self.path}"
        if url.startswith(prefix) and url.endswith(suffix):
            return url[len(prefix) : -len(suffix)]
        return None


@dataclass(frozen=True)
class Config:
    root: Path
    master: Master
    project: Project
    node: Node
    software: dict[str, Software]

    def software_named(self, name: str | None) -> Software:
        if name is None:
            if len(self.software) != 1:
                msg = f"several software in {FILENAME}, name one of: {', '.join(self.software)}"
                raise ConfigError(msg)
            return next(iter(self.software.values()))
        if name in self.software:
            return self.software[name]
        for sw in self.software.values():
            if sw.instance == name:
                return sw
        msg = f"no software or instance {name!r} in {FILENAME} (software: {', '.join(self.software)})"
        raise ConfigError(msg)

    def url_name(self, url: str) -> str:
        """A software release URL as hox.toml knows it ("sbe branch", "sbe @ 971d944c2"), else the URL."""
        for sw in self.software.values():
            if ref := sw.ref_of(url):
                return (
                    f"{sw.name} branch"
                    if ref == sw.branch
                    else f"{sw.name} @ {short_ref(ref)}"
                )
        return url


def find(start: Path) -> Path:
    for directory in (start, *start.parents):
        if (directory / FILENAME).is_file():
            return directory / FILENAME
    msg = f"no {FILENAME} in {start} or its parents"
    raise ConfigError(msg)


def load(path: Path | None = None) -> Config:
    path = (path or find(Path.cwd())).resolve()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        msg = f"{path}: {e}"
        raise ConfigError(msg) from e
    return parse(data, path.parent)


def parse(data: dict, root: Path) -> Config:
    data = dict(data)

    def resolve(value: str) -> Path:
        return (root / Path(value).expanduser()).resolve()

    def ssh_args(args: tuple[str, ...]) -> tuple[str, ...]:
        # The files of ssh's -F (config) and -i (key) resolve against hox.toml's directory.
        return tuple(
            str(resolve(a)) if prev in {"-F", "-i"} else a
            for prev, a in zip(("",) + args, args)
        )

    master = _build(Master, _table(data, "master"), "master")
    master = dataclasses.replace(
        master, cert=resolve(master.cert), key=resolve(master.key)
    )
    project = _build(Project, _table(data, "project"), "project")
    node = _build(Node, _table(data, "node"), "node")
    node = dataclasses.replace(node, ssh=ssh_args(node.ssh))

    software = {}
    for name, table in _table(data, "software").items():
        ctx = f"software.{name}"
        table = dict(table)
        checks = tuple(
            _build(Check, c, f"{ctx}.checks[{i}]")
            for i, c in enumerate(table.pop("checks", []))
        )
        if not all(isinstance(v, str) for v in table.get("parameters", {}).values()):
            msg = f"[{ctx}.parameters] values must be strings"
            raise ConfigError(msg)
        sw = _build(Software, {"name": name, **table, "checks": checks}, ctx)
        software[name] = dataclasses.replace(sw, checkout=resolve(sw.checkout))
    if not software:
        msg_0 = "[software.<name>]: at least one is required"
        raise ConfigError(msg_0)
    if data:
        msg = f"unknown sections: {', '.join(data)}"
        raise ConfigError(msg)
    return Config(root, master, project, node, software)


def _table(data: dict, name: str) -> dict:
    if name not in data:
        msg = f"[{name}] is required"
        raise ConfigError(msg)
    value = data.pop(name)
    if not isinstance(value, dict):
        msg = f"[{name}] must be a table"
        raise ConfigError(msg)
    return value


def _build(cls, table: dict, ctx: str):
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(table) - set(fields)
    if unknown:
        msg = f"[{ctx}] unknown keys: {', '.join(sorted(unknown))}"
        raise ConfigError(msg)
    required = {
        n
        for n, f in fields.items()
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    missing = required - set(table)
    if missing:
        msg = f"[{ctx}] missing keys: {', '.join(sorted(missing))}"
        raise ConfigError(msg)
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return cls(**values)
