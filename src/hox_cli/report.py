"""`hox build-report`: how long a software's builds took on the node, and the disk they use, as Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .node import SEPARATOR, SOFTWARE_ROOT, Node, software_dir
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config, Software

LOG_DIR = "/opt/slapos/log"
SHARED = f"{SOFTWARE_ROOT}/shared"
CRON = "/etc/cron.d/slapos-node"
LONGEST = 15  # parts listed per run; the others are summed up
LARGEST = 10  # shared parts listed

# What a `slapos node software` log says about a build (slapgrid and buildout lines).
_EVENT = re.compile(
    r"^\[(?P<time>[\d-]+ [\d:]+),\d+\] \w+ +(?:"
    r"Installing software release (?P<release>\S+) \.\.\."
    r"|Installing (?P<part>[\w.+-]+)\."
    r"|.*\"POST /(?P<available>availableSoftwareRelease) .*"
    r"|(?P<failed>Failed to run buildout profile) .*"
    r"|(?P<finished>Finished software releases\.)"
    r"|(?P<processing>Processing software releases\.\.\.))$"
)


def utc(text: str) -> datetime:
    """A time of the node's logs or `date -u`: the node runs in UTC."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


_GREP = (
    r"Installing software release |INFO +Installing [A-Za-z0-9_.+-]+\.$"
    r"|POST /availableSoftwareRelease|Failed to run buildout profile|(Finished|Processing) software releases"
)


@dataclass
class Run:
    """One `slapos node software` attempt at a release URL."""

    url: str
    start: datetime
    end: datetime | None = None  # None: still running when the log was read
    result: str = (
        "building"  # "completed", "failed", "stopped" (killed, no result) or "building"
    )
    parts: list[tuple[str, datetime]] = field(
        default_factory=list
    )  # (part, when its install started)

    def part_times(self, now: datetime) -> list[tuple[str, float]]:
        """(part, seconds), each part lasting until the next one starts or the run ends."""
        ends = [t for _, t in self.parts[1:]] + [self.end or now]
        return [
            (name, (end - start).total_seconds())
            for (name, start), end in zip(self.parts, ends)
        ]


def parse_runs(log: str) -> list[Run]:
    runs: list[Run] = []
    current: Run | None = None
    last: datetime | None = None  # time of the last line read

    def close(run: Run, end: datetime) -> None:
        run.end = end
        if run.result == "building":
            run.result = "stopped"

    for line in log.splitlines():
        m = _EVENT.match(line)
        if not m:
            continue
        when = utc(m["time"])
        if m["processing"]:  # a new slapgrid run: one still open was killed
            if current:
                assert last is not None  # an open run has had at least its first line
                close(current, last)
            current = None
        elif m["release"]:
            if current:  # the previous release of the same slapgrid run
                close(current, when)
            current = Run(m["release"], when)
            runs.append(current)
        elif current is None:
            pass
        elif m["part"]:
            current.parts.append((m["part"], when))
        elif m["available"]:
            current.result = "completed"
        elif m["failed"]:
            current.result = "failed"
        elif m["finished"]:
            close(current, when)
            current = None
        last = when
    return runs


@dataclass(frozen=True)
class NodeUsage:
    now: datetime
    cpus: int
    memory_mib: int
    makeflags: str | None
    disk_kib: tuple[int, int]  # (used, size) of the filesystem of /opt/slapgrid
    sizes_kib: dict[str, int]  # path -> size, for each release dir and each shared part


def gather(node: Node) -> tuple[list[Run], NodeUsage]:
    """One SSH round trip: the build lines of the software logs (rotated ones first), then sizes."""
    output = node.run(
        "; ".join([
            f"cd {LOG_DIR}",
            f"cat $(ls slapos-node-software.log-* 2>/dev/null) slapos-node-software.log | grep -E '{_GREP}'",
            f"echo {SEPARATOR}",
            "date -u '+%Y-%m-%d %H:%M:%S'; nproc; free -m | awk '/^Mem/ {print $2}'",
            f"grep -h '^MAKEFLAGS=' {CRON} || echo",
            f"df -k --output=used,size {SOFTWARE_ROOT} | tail -1",
            f"echo {SEPARATOR}",
            f"du -sk {SOFTWARE_ROOT}/[0-9a-f]* {SHARED}/* 2>/dev/null || true",  # release dirs: md5 hex names
        ])
    )
    log, facts, sizes = output.split(SEPARATOR + "\n")
    now, cpus, memory, makeflags, disk = facts.splitlines()
    used, size = disk.split()
    usage = NodeUsage(
        utc(now),
        int(cpus),
        int(memory),
        makeflags.removeprefix("MAKEFLAGS=") or None,
        (int(used), int(size)),
        {
            path: int(kib)
            for kib, path in (line.split("\t") for line in sizes.splitlines())
        },
    )
    return parse_runs(log), usage


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def size(kib: int) -> str:
    return f"{kib / 1024**2:.1f} GiB" if kib >= 1024**2 else f"{kib / 1024:.0f} MiB"


def markdown(config: Config, sw: Software, runs: list[Run], usage: NodeUsage) -> str:
    ours = [r for r in runs if sw.ref_of(r.url) is not None]
    out = [f"# Build of {sw.name} on {config.node.title}", ""]
    out.append(
        f"Node: {usage.cpus} vCPU, {usage.memory_mib / 1024:.1f} GiB of memory,"
        f" make jobs now: {usage.makeflags or 'one (no MAKEFLAGS)'} (a run keeps the setting it started with)."
        f" Read on {usage.now:%Y-%m-%d %H:%M} UTC."
    )
    if not ours:
        out += ["", f"No build of {sw.name} in the node's software logs."]
    for url in dict.fromkeys(r.url for r in ours):
        url_runs = [r for r in ours if r.url == url]
        out += ["", f"## {config.url_name(url)}", "", f"<{url}>", ""]
        out += [
            "| Run | Started (UTC) | Ended (UTC) | Duration | Result |",
            "|---|---|---|---|---|",
        ]
        for i, r in enumerate(url_runs, 1):
            end = f"{r.end:%Y-%m-%d %H:%M}" if r.end else "running"
            spent = duration(((r.end or usage.now) - r.start).total_seconds())
            out.append(
                f"| {i} | {r.start:%Y-%m-%d %H:%M} | {end} | {spent} | {r.result} |"
            )
        total = sum(((r.end or usage.now) - r.start).total_seconds() for r in url_runs)
        out += ["", f"Total build time: {duration(total)} over {len(url_runs)} run(s)."]
        longest = max(url_runs, key=lambda r: len(r.parts))
        times = sorted(longest.part_times(usage.now), key=lambda p: -p[1])
        if times:
            number = url_runs.index(longest) + 1
            out += [
                "",
                f'Longest parts of run {number} (from buildout\'s "Installing" lines):',
                "",
            ]
            out += ["| Part | Time |", "|---|---|"]
            out += [f"| {name} | {duration(t)} |" for name, t in times[:LONGEST]]
            if rest := times[LONGEST:]:
                out.append(
                    f"| {len(rest)} other parts | {duration(sum(t for _, t in rest))} |"
                )
    shared = {p: k for p, k in usage.sizes_kib.items() if p.startswith(SHARED + "/")}
    largest = sorted(shared.items(), key=lambda p: -p[1])[:LARGEST]
    out += ["", "## Disk", "", "| What | Size |", "|---|---|"]
    for url in dict.fromkeys(r.url for r in ours):
        if (kib := usage.sizes_kib.get(software_dir(url))) is not None:
            out.append(f"| {config.url_name(url)}: release directory | {size(kib)} |")
    out.append(
        f"| Shared parts, for every release on the node ({len(shared)}) | {size(sum(shared.values()))} |"
    )
    out += [
        f"| &nbsp;&nbsp;{p.removeprefix(SHARED + '/')} | {size(k)} |"
        for p, k in largest
    ]
    used, total = usage.disk_kib
    out.append(f"| Node disk used (all of it) | {size(used)} of {size(total)} |")
    return "\n".join(out)
