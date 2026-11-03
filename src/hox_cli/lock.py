"""From a project's uv.lock to buildout's [versions]: what the project needs at run
time on a SlapOS node, pinned as uv resolved it, and what buildout can't install
as is."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging import tags
from packaging.markers import Marker
from packaging.utils import canonicalize_name, parse_wheel_filename


# buildout's own: their versions are SlapOS's (stack/slapos.cfg), whatever the app wants.
BUILDOUT_TOOLS = frozenset({"pip", "setuptools", "wheel", "zc-buildout"})

# Built by SlapOS's stack itself, from source, for slapos.cookbook (stack/slapos.cfg):
# a :whl pin breaks that build.
STACK_BUILT = frozenset({"cffi", "lxml", "pyyaml"})


class LockError(Exception):
    pass


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    sdist: bool
    wheel: str | None  # "pure", "binary" (manylinux, for the node), or none for the node

    @property
    def stack_built(self) -> bool:
        return canonicalize_name(self.name) in STACK_BUILT

    @property
    def constraint(self) -> str:
        # A wheel installs without a build: buildout builds sdists with the build's
        # setuptools (74 on SlapOS 1.0), which many recent sdists are too new for.
        return f"{self.version}:whl" if self.wheel and not self.stack_built else self.version


def node_tags(python: str) -> frozenset[tags.Tag]:
    """The wheel tags a SlapOS node's CPython `python` installs: manylinux x86_64
    (the node's glibc, Debian 12, is 2.36)."""
    version = tuple(int(n) for n in python.split(".")[:2])
    platforms = [f"manylinux_2_{minor}_x86_64" for minor in range(36, 4, -1)]
    platforms += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64", "linux_x86_64"]
    return frozenset([*tags.cpython_tags(version, platforms=platforms),
                      *tags.compatible_tags(version, platforms=platforms)])


def wheel_kind(filenames: list[str], supported: frozenset[tags.Tag]) -> str | None:
    found = {t for f in filenames for t in parse_wheel_filename(f)[3]} & supported
    if any(t.platform == "any" for t in found):
        return "pure"
    return "binary" if found else None


def environment(python: str) -> dict[str, str]:
    """Marker values on a SlapOS node: Linux x86_64, CPython `python` (3.12)."""
    return {
        "python_version": ".".join(python.split(".")[:2]),
        "python_full_version": python,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "sys_platform": "linux",
        "platform_system": "Linux",
        "platform_machine": "x86_64",
        "os_name": "posix",
    }


def runtime_pins(lock: dict, python: str) -> list[Pin]:
    """The root project's run-time dependencies (no dependency groups), with the
    extras they ask for, whose markers hold on the node."""
    env = environment(python)
    supported = node_tags(python)
    packages: dict[str, list[dict]] = {}
    for p in lock["package"]:
        packages.setdefault(p["name"], []).append(p)
    roots = [p for p in lock["package"] if {"editable", "virtual"} & p["source"].keys()]
    if len(roots) != 1:
        raise LockError(f"expected one editable or virtual root package, found {len(roots)}")
    root = roots[0]

    def holds(dep: dict, extra: str = "") -> bool:
        return "marker" not in dep or Marker(dep["marker"]).evaluate(env | {"extra": extra})

    def package(dep: dict) -> dict:
        found = packages[dep["name"]]
        if len(found) > 1:  # uv forked the resolution: the dependency says which
            found = [p for p in found if p["version"] == dep["version"]]
        return found[0]

    seen: set[tuple[str, str]] = set()
    pins: dict[str, Pin] = {}
    todo = [(root, "")]
    while todo:
        p, extra = todo.pop()
        if (p["name"], extra) in seen:
            continue
        seen.add((p["name"], extra))
        deps = p.get("optional-dependencies", {}).get(extra, []) if extra else p.get("dependencies", [])
        for dep in deps:
            if not holds(dep, extra):
                continue
            q = package(dep)
            todo += [(q, "")] + [(q, e) for e in dep.get("extra", [])]
        if p is not root and not extra and p["name"] not in BUILDOUT_TOOLS:
            wheels = [w["url"].rsplit("/", 1)[1] for w in p.get("wheels", [])]
            pins[p["name"]] = Pin(p["name"], p["version"], "sdist" in p, wheel_kind(wheels, supported))
    return sorted(pins.values(), key=lambda pin: pin.name)


def versions_cfg(path: Path, python: str) -> str:
    """A [versions] section for buildout, with what needs attention as comments."""
    return render_versions(runtime_pins(tomllib.loads(path.read_text()), python), f"{path.name} by `hox versions`", python)


def render_versions(pins: list[Pin], source: str, python: str) -> str:
    binary = [p.name for p in pins if p.wheel == "binary" and not p.stack_built]
    stack = [p.name for p in pins if p.stack_built]
    compiled = [p.name for p in pins if not p.wheel and p.sdist]
    unbuildable = [p.name for p in pins if not p.wheel and not p.sdist]
    lines = [f"# From {source}, for CPython {python} on Linux x86_64.",
             "# Packages with a wheel for the node are installed from it (:whl)."]
    if binary:
        lines += ["# From binary (manylinux) wheels, with their C libraries bundled, not",
                  "# SlapOS's: a security fix in one comes with a new release of the package.",
                  *_wrapped(binary)]
    if stack:
        lines += ["# Built by SlapOS's stack (slapos.cookbook), from source, with its recipe:",
                  *_wrapped(stack)]
    if compiled:
        lines += ["# Built from their sdist (no wheel for the node): those with C or Rust",
                  "# code need their libraries and build tools in the build:", *_wrapped(compiled)]
    if unbuildable:
        lines += ["# No sdist and no wheel for the node, so buildout can't install them:",
                  "# replace them (another extra, or a SlapOS component):", *_wrapped(unbuildable)]
    width = max(len(p.name) for p in pins)
    return "\n".join([*lines, "", "[versions]", *(f"{p.name:<{width}} = {p.constraint}" for p in pins), ""])


def _wrapped(names: list[str]) -> list[str]:
    lines, line = [], "#  "
    for name in names:
        if len(line) + len(name) > 76:
            lines.append(line)
            line = "#  "
        line += f" {name}"
    return [*lines, line]
