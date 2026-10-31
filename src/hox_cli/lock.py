"""From a project's uv.lock to buildout's [versions]: what the project needs at run
time on a SlapOS node, pinned as uv resolved it, and what buildout can't install
as is."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import Marker


# buildout's own: their versions are SlapOS's (stack/slapos.cfg), whatever the app wants.
BUILDOUT_TOOLS = frozenset({"pip", "setuptools", "wheel", "zc-buildout"})


class LockError(Exception):
    pass


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    sdist: bool
    pure: bool  # has a py3-none-any wheel

    @property
    def constraint(self) -> str:
        # A pure wheel installs without a build, whatever its build backend:
        # buildout builds sdists with what the build's Python has.
        return f"{self.version}:whl" if self.pure else self.version


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
            pins[p["name"]] = Pin(p["name"], p["version"], "sdist" in p,
                                  any(w.endswith("-none-any.whl") for w in wheels))
    return sorted(pins.values(), key=lambda pin: pin.name)


def versions_cfg(path: Path, python: str) -> str:
    """A [versions] section for buildout, with what needs attention as comments."""
    pins = runtime_pins(tomllib.loads(path.read_text()), python)
    compiled = [p.name for p in pins if not p.pure and p.sdist]
    unbuildable = [p.name for p in pins if not p.pure and not p.sdist]
    lines = [f"# From {path.name} by `hox versions`, for CPython {python} on Linux x86_64.",
             "# Pure-Python packages are installed from their wheels (:whl)."]
    if compiled:
        lines += ["# Built from their sdist: those with C or Rust code need their libraries",
                  "# and build tools in the build (SlapOS components):", *_wrapped(compiled)]
    if unbuildable:
        lines += ["# No sdist and no pure wheel, so buildout can't install them: replace them",
                  "# (another extra, or a SlapOS component):", *_wrapped(unbuildable)]
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
