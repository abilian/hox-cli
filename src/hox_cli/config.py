"""hox.toml, parsed once into frozen dataclasses."""

import dataclasses
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
                raise ConfigError(f"several software in {FILENAME}, name one of: {', '.join(self.software)}")
            return next(iter(self.software.values()))
        if name not in self.software:
            raise ConfigError(f"no software {name!r} in {FILENAME} (have: {', '.join(self.software)})")
        return self.software[name]

    def url_name(self, url: str) -> str:
        """A software release URL as hox.toml knows it ("sbe branch", "sbe @ 971d944c2"), else the URL."""
        for sw in self.software.values():
            if ref := sw.ref_of(url):
                return f"{sw.name} branch" if ref == sw.branch else f"{sw.name} @ {ref[:9]}"
        return url


def find(start: Path) -> Path:
    for directory in (start, *start.parents):
        if (directory / FILENAME).is_file():
            return directory / FILENAME
    raise ConfigError(f"no {FILENAME} in {start} or its parents")


def load(path: Path | None = None) -> Config:
    path = (path or find(Path.cwd())).resolve()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(data, path.parent)


def parse(data: dict, root: Path) -> Config:
    data = dict(data)

    def resolve(value: str) -> Path:
        return (root / Path(value).expanduser()).resolve()

    def ssh_args(args: tuple[str, ...]) -> tuple[str, ...]:
        # The files of ssh's -F (config) and -i (key) resolve against hox.toml's directory.
        return tuple(str(resolve(a)) if prev in ("-F", "-i") else a for prev, a in zip(("",) + args, args))

    master = _build(Master, _table(data, "master"), "master")
    master = dataclasses.replace(master, cert=resolve(master.cert), key=resolve(master.key))
    project = _build(Project, _table(data, "project"), "project")
    node = _build(Node, _table(data, "node"), "node")
    node = dataclasses.replace(node, ssh=ssh_args(node.ssh))

    software = {}
    for name, table in _table(data, "software").items():
        ctx = f"software.{name}"
        table = dict(table)
        checks = tuple(
            _build(Check, c, f"{ctx}.checks[{i}]") for i, c in enumerate(table.pop("checks", []))
        )
        sw = _build(Software, {"name": name, **table, "checks": checks}, ctx)
        software[name] = dataclasses.replace(sw, checkout=resolve(sw.checkout))
    if not software:
        raise ConfigError("[software.<name>]: at least one is required")
    if data:
        raise ConfigError(f"unknown sections: {', '.join(data)}")
    return Config(root, master, project, node, software)


def _table(data: dict, name: str) -> dict:
    if name not in data:
        raise ConfigError(f"[{name}] is required")
    value = data.pop(name)
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _build(cls, table: dict, ctx: str):
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(table) - set(fields)
    if unknown:
        raise ConfigError(f"[{ctx}] unknown keys: {', '.join(sorted(unknown))}")
    required = {
        n for n, f in fields.items()
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    missing = required - set(table)
    if missing:
        raise ConfigError(f"[{ctx}] missing keys: {', '.join(sorted(missing))}")
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return cls(**values)
