"""The `hox` command."""

import dataclasses
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import httpx2
from cyclopts import App, Parameter

from . import config as config_module
from . import deploy as deploy_module
from . import dev, lock, panel
from . import port as port_module
from . import report as report_module
from . import status as status_module
from . import try_start as try_start_module
from .config import Config, ConfigError, Software
from .dev import DevError
from .lock import LockError
from .master import (
    Api,
    ComputeNode,
    MasterError,
    Request,
    Slap,
    find_node,
    next_name,
    stored_request,
)
from .node import (
    INSTANCE_LOG,
    SLAPGRID_PROMISE_FAIL,
    SOFTWARE_LOG,
    Node,
    NodeError,
)
from .port import PortError

app = App(
    name="hox",
    help="Deploy and manage applications on SlapOS (Rapid.Space) from the terminal.",
)
api_app = App(name="api", help="Raw, read-only access to the master's API.")
app.command(api_app)

_config_path: Path | None = None


def _config() -> Config:
    return config_module.load(_config_path)


def _api(config: Config) -> Api:
    return Api.connect(config.master)


def _node(config: Config, api: Api) -> ComputeNode:
    return find_node(api, config.project.reference, config.node.title)


def _print_json(data) -> None:
    print(json.dumps(data, indent=1, default=str))


@api_app.command
def get(relative_url: str, *, view: str | None = None):
    """Print a document (ERP5 traverse), e.g. instance_tree_module/20261029.012-427166F0.

    Parameters
    ----------
    relative_url
        The document's relative URL.
    view
        A view name from the document's links (view, slapos_panel_view, …).
    """
    _print_json(_api(_config()).get(relative_url, view))


@api_app.command
def search(
    query: str, *, select: tuple[str, ...] = ("title", "relative_url"), limit: int = 100
):
    """Print catalog search results, e.g. 'portal_type:"Instance Tree" AND title:="my-app-1"'.

    Parameters
    ----------
    query
        An ERP5 catalog query.
    select
        Columns to return (repeat the option for several).
    limit
        Maximum number of rows.
    """
    _print_json(_api(_config()).search(query, list(select), limit))


@app.command
def versions(lock_file: Path = Path("uv.lock"), *, python: str = "3.12"):
    """Print a buildout [versions] section from a project's uv.lock.

    The root project's run-time dependencies (no dev groups), pinned as uv
    resolved them, with the packages buildout must build from source, or can't
    install, listed in comments. Needs no hox.toml.

    Parameters
    ----------
    lock_file
        The project's uv.lock.
    python
        The release's Python, for the dependencies' markers.
    """
    print(lock.versions_cfg(lock_file, python), end="")


@app.command
def port(
    recipe: Path,
    slapos: Path,
    *,
    python: str = "3.13",
    source: Path | None = None,
    venv: bool = False,
):
    """Write a SlapOS software release from a Hop3 recipe, on the hop3 stack.

    The release goes to <slapos>/software/<app id>/: a software.cfg, a
    versions.cfg from the recipe's requirements.txt (with PyPI's wheels), and
    the recipe's files in hop3/. What couldn't be mapped is listed. Needs no
    hox.toml.

    Parameters
    ----------
    recipe
        The recipe's directory, with its hop3.toml (a catalog app's directory).
    slapos
        A checkout of slapos_abilian_edge with stack/hop3/.
    python
        The release's Python.
    source
        For an app installed from its source (no requirements.txt): an unpacked
        copy of the release its recipe fetches. Its dependencies are resolved with uv.
    venv
        Install the app with pip in a venv of its own, compiled packages from their
        sdists, in place of buildout's eggs: goes to <slapos>/software/<app id>-venv/,
        with a requirements.txt in place of the versions.cfg.
    """
    if not (slapos / "stack" / "hop3").is_dir():
        raise PortError(f"{slapos}: no stack/hop3/ (branch hop3-experiment?)")
    release = port_module.port(recipe, python, source=source, venv=venv)
    out = slapos / "software" / (f"{release.name}-venv" if venv else release.name)
    port_module.write(release, out)
    pins = "requirements.txt, " if venv else "versions.cfg, " if release.pins else ""
    print(f"{out}: software.cfg, {pins}hop3/ ({len(release.files)} files)")
    for note in release.notes:
        print(f"  {note}")


@app.command
def build_report(software: str | None = None):
    """How long the software's builds took on the node, and the disk they use, as Markdown.

    From the node's software logs (14 days are kept): each build run of each
    of its release URLs, the longest parts, then the release and shared part
    sizes.

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    """
    config = _config()
    sw = config.software_named(software)
    runs, usage = report_module.gather(Node(config.node.ssh))
    print(report_module.markdown(config, sw, runs, usage))


def _overview(config: Config) -> status_module.Overview:
    api = _api(config)
    return status_module.overview(
        config, api, Node(config.node.ssh), _node(config, api).reference
    )


@app.command
def instances(
    *,
    long: Annotated[bool, Parameter(name=["--long", "-l"])] = False,
    json_: Annotated[bool, Parameter(name="--json")] = False,
):
    """List the project's instances, and whether their services run on the node.

    `up` means their processes run: `hox check` tests that the app answers.

    Parameters
    ----------
    long
        Add each one's software in hox.toml, release and partition.
    json_
        Print the list as JSON, with everything.
    """
    config = _config()
    result = _overview(config)
    if json_:
        _print_json([dataclasses.asdict(row) for row in result.instances])
    else:
        print(format_instances(result, long=long))


@app.command
def status(
    software: str | None = None,
    *,
    json_: Annotated[bool, Parameter(name="--json")] = False,
):
    """The whole system, or one software in detail.

    Without a name: the node's uptime, partitions, memory and disk, the builds that
    aren't complete, the failing promises, the services down, the instances. With one:
    its instance, services, frontends, builds on the node, promises and what GitHub serves.

    Parameters
    ----------
    software
        A software of hox.toml or an instance's name, to see it in detail.
    json_
        Print the report as JSON.
    """
    config = _config()
    if software is None:
        result = _overview(config)
        if json_:
            _print_json(dataclasses.asdict(result))
        else:
            print(format_overview(result, config))
        return
    sw = config.software_named(software)
    with httpx2.Client(timeout=30) as http:
        api = _api(config)
        report = status_module.gather(
            config, sw, api, Node(config.node.ssh), http, _node(config, api).reference
        )
    if json_:
        _print_json(dataclasses.asdict(report))
    else:
        print(format_report(report, config))


def format_instances(o: status_module.Overview, *, long: bool = False) -> str:
    def row(r: status_module.Deployed) -> tuple[str, ...]:
        extra = (r.software or "-", r.release, r.partition or "-") if long else ()
        memory = "-" if r.memory_mb is None else str(r.memory_mb)
        return (r.instance, *extra, r.status, memory, r.frontend or "-")

    header = (
        "INSTANCE",
        *(("SOFTWARE", "RELEASE", "PARTITION") if long else ()),
        "STATUS",
        "MB",
        "FRONTEND",
    )
    rows = [header, *map(row, o.instances)]
    widths = [max(map(len, column)) for column in zip(*rows)]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(r, widths)).rstrip()
        for r in rows
    ]
    if o.node_error:
        lines.insert(0, f"node unreachable, so what runs is unknown: {o.node_error}\n")
    return "\n".join(lines)


def _duration(seconds: int) -> str:
    days, hours, minutes = seconds // 86400, seconds // 3600 % 24, seconds // 60 % 60
    return f"{days} d {hours} h" if days else f"{hours} h {minutes} min"


def format_overview(o: status_module.Overview, config: Config) -> str:
    out = []
    if s := o.system:
        used = {r.partition for r in o.instances}
        free = [p.name for p in s.partitions if p.name not in used]
        out.append(
            f"node           {config.node.title}: up {_duration(s.uptime_seconds)}"
        )
        out.append(
            f"partitions     {len(s.partitions) - len(free)} of {len(s.partitions)} in use"
            + (f", free: {', '.join(free)}" if free else "")
        )
        out.append(
            f"memory         {s.memory_available_mb} MB available of {s.memory_total_mb} MB"
        )
        out.append(
            f"disk           {s.disk_free_mb // 1024} GB free of {s.disk_total_mb // 1024} GB"
        )
    if f := o.facts:
        built = sum(b.state == "completed" for b in f.software)
        out.append(f"builds         {built} of {len(f.software)} complete")
        for b in f.software:
            if b.state != "completed":
                out.append(
                    f"build          {config.url_name(b.url)}: {b.state}"
                    + (f" (last error: {b.last_error})" if b.last_error else "")
                )
        # Their output is for `hox status NAME`.
        out += [
            f"promise        {re.sub(r"Promise '([^']+)' failed.*", r'\1', p)}"
            for p in f.failing_promises
        ]
        if not f.failing_promises:
            out.append("promises       all OK in the last instance run")
    # All of a partition's services are down while the node boots: its status says enough.
    out += [
        f"down           {r.instance}: {', '.join(r.down)}"
        for r in o.instances
        if r.down and r.status != "not running"
    ]
    if o.undeployed:
        out.append(f"not deployed   {', '.join(o.undeployed)}")
    return "\n".join([*out, "", format_instances(o)] if out else [format_instances(o)])


def format_report(r: status_module.Report, config: Config) -> str:
    out = []
    i = r.instance
    if i is None:
        out.append("instance       none (`hox request` to create it)")
    else:
        out.append(
            f"instance       {i.title}: {i.state}, {config.url_name(i.url)}, type {i.software_type}"
        )
        out.append(f"partition      {i.partition or 'not allocated'}")
        if s := r.services:
            out.append(
                f"services       {s.running} running, {s.memory_mb} MB"
                + (f", down: {', '.join(s.down)}" if s.down else "")
            )
        out += [f"diagnosis      {d}" for d in r.diagnosis]
        if i.news:
            out.append(f"news           {i.news}")
        for key, value in sorted(i.connection.items()):
            out.append(f"connection     {key} = {value}")
        for f in i.frontends:
            out.append(
                f"frontend       {f.title}: {'allocated' if f.allocated else 'not allocated'}"
            )
    states = {s.url: s for s in (r.node.software if r.node else [])}
    for inst in r.installations:
        node_state = states.get(inst.url)
        line = f"software       {config.url_name(inst.url)}: master {inst.state}"
        if node_state:
            line += f", node {node_state.state}"
            if node_state.last_error:
                line += f" (last error: {node_state.last_error})"
        out.append(line)
    if r.node_error:
        out.append(f"node           unreachable: {r.node_error}")
    elif r.node is not None:
        if r.node.failing_promises:
            out += [f"promise        {p}" for p in r.node.failing_promises]
        else:
            out.append("promises       all OK in the last instance run")
    if r.github:
        g = r.github
        served = {
            True: "serves it",
            False: "serves an older file (cache: up to 5 min)",
            None: "could not be fetched",
        }
        out.append(
            f"github         local head {g.local_head[:9]}, {'pushed' if g.pushed else 'NOT pushed'},"
            f" raw.githubusercontent.com {served[g.served]}"
        )
    return "\n".join(out)


@app.command
def check(software: str | None = None) -> int:
    """Run the software's checks (hox.toml) with curl on the node.

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    """
    config = _config()
    sw = config.software_named(software)
    inst = status_module.require_instance(_api(config), sw)
    return _report_checks(
        dev.run_checks(sw.checks, inst.connection, Node(config.node.ssh))
    )


def _report_checks(results: list[dev.CheckResult]) -> int:
    for r in results:
        print(
            f"{'ok  ' if r.ok else 'FAIL'}  {r.name}: {r.detail}"
            + (f"  ({r.url})" if r.url else "")
        )
    return 0 if all(r.ok for r in results) else 1


def _require_published(sw: Software, *, wait: bool = True) -> None:
    """Refuse an unpushed branch: the node would get a 404, or an older release."""
    head = dev.require_pushed(sw)
    print(f"{sw.branch} is pushed at {head[:9]}")
    if wait:
        with httpx2.Client(timeout=30) as http:
            dev.wait_for_github(sw, head, http)


@app.command
def rebuild(
    software: str | None = None, *, software_only: bool = False, no_wait: bool = False
) -> int:
    """The dev loop: rebuild the branch URL in place on the node, redeploy, check.

    Refuses if the local branch isn't pushed. Waits until raw.githubusercontent.com
    serves the pushed files (its cache keeps them up to 5 minutes).

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    software_only
        Rebuild the software release only: no instance run, no checks.
    no_wait
        Don't wait for raw.githubusercontent.com to serve the pushed files.
    """
    config = _config()
    sw = config.software_named(software)
    _require_published(sw, wait=not no_wait)
    node = Node(config.node.ssh)
    node.slapgrid(f"software --force --only-sr {sw.branch_url}")
    if software_only:
        return 0
    inst = status_module.require_instance(_api(config), sw)
    if inst.partition is None:
        raise DevError(f"{sw.instance} is not allocated yet: `hox status` says why")
    # Only this instance's partition: another one's failure (a release still building) isn't ours.
    if (
        node.slapgrid(f"instance --force --only-cp {inst.partition}")
        == SLAPGRID_PROMISE_FAIL
    ):
        print("some promises fail (see above)")
    inst = status_module.require_instance(
        _api(config), sw
    )  # connection parameters, as published by this run
    return _report_checks(dev.run_checks(sw.checks, inst.connection, node))


@app.command
def try_start(software: str, *, timeout: int = 300) -> int:
    """Start a test build's real processes on the node, and see whether they come up.

    What `/srv/hox-dev/run-build.sh` built: the release's own scripts, in a temporary
    directory, with its haproxy and a PostgreSQL of their own. Stops at the first
    failure, with the logs. No frontend, nothing asked of the master.

    Parameters
    ----------
    software
        The directory of the build in /srv/hox-dev: a release's name.
    timeout
        Seconds to wait for the app to answer.
    """
    source = Path(try_start_module.__file__).read_bytes()
    python = f"cd {try_start_module.BUILDS} && /opt/slapos/parts/python3/bin/python3 -u - {shlex.quote(software)} {timeout}"
    command = f"su -s /bin/sh slapsoft -c {shlex.quote(python)}"
    return subprocess.run(
        [*_config().node.ssh, command], input=source, check=False
    ).returncode


@app.command
def logs(
    kind: Literal["software", "instance", "app"],
    *,
    software: str | None = None,
    follow: Annotated[bool, Parameter(name=["--follow", "-f"])] = False,
    lines: Annotated[int, Parameter(name=["--lines", "-n"])] = 50,
) -> int:
    """Tail a log on the node: slapgrid's software or instance log, or the app's service logs.

    Parameters
    ----------
    kind
        software, instance, or app (the instance's service logs).
    software
        A [software.<name>] of hox.toml, for app logs; optional when there is only one.
    follow
        Keep following (Ctrl-C to stop).
    lines
        Number of lines to show.
    """
    config = _config()
    if kind == "app":
        sw = config.software_named(software)
        partition = status_module.require_instance(_api(config), sw).partition
        if partition is None:
            raise DevError(f"{sw.instance} has no partition yet")
        target = f"/srv/slapgrid/{partition}/.{partition}_*.log"
    else:
        target = SOFTWARE_LOG if kind == "software" else INSTANCE_LOG
    return Node(config.node.ssh).interactive(
        f"tail -n {lines}{' -F' if follow else ''} {target}"
    )


def _url(sw: Software, pinned: str | None) -> str:
    return sw.pinned_url(dev.pushed_commit(sw, pinned)) if pinned else sw.branch_url


def _confirm(question: str, yes: bool) -> None:
    if not yes and input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise DevError("cancelled")


@app.command
def supply(
    software: str | None = None, *, pinned: str | None = None, remove: bool = False
):
    """Supply the software release on your node (or remove it).

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    pinned
        A commit: use its pinned URL instead of the branch URL.
    remove
        Remove the release from the node instead.
    """
    config = _config()
    url = _url(config.software_named(software), pinned)
    node = _node(config, _api(config))
    Slap.connect(config.master).supply(
        url, node.reference, "destroyed" if remove else "available"
    )
    print(f"{'removal' if remove else 'supply'} of {url} on {node.reference} requested")


@app.command
def request(
    software: str | None = None,
    *,
    name: str | None = None,
    pinned: str | None = None,
    replace: bool = False,
    yes: bool = False,
):
    """Request the instance on your node, with the branch URL or a pinned one.

    Re-requesting an existing instance re-sends its original request. The master
    refuses to change an instance's URL (HTTP 500): --replace destroys it and
    requests the next name instead (my-app-1 -> my-app-2).

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    name
        Instance name (default: `instance` in hox.toml).
    pinned
        A commit: use its pinned URL instead of the branch URL.
    replace
        If the instance runs another URL, destroy it and request the next name.
    yes
        Don't ask before destroying.
    """
    config = _config()
    sw = config.software_named(software)
    url = _url(sw, pinned)
    name = name or sw.instance
    api, slap = _api(config), Slap.connect(config.master)
    existing = stored_request(api, name)
    if existing and existing.software_release != url:
        if not replace:
            raise DevError(
                f"{name} runs {existing.software_release}; the master refuses a URL change: use --replace"
            )
        _confirm(f"Destroy {name} and request {next_name(name)}?", yes)
        slap.request(dataclasses.replace(existing, state="destroyed"))
        print(f"{name}: destruction requested")
        name, existing = next_name(name), None
        while stored_request(api, name):
            name = next_name(name)
    req = existing or Request(
        url, name, sw.software_type, {"computer_guid": _node(config, api).reference}
    )
    if sw.parameters is not None:
        req = dataclasses.replace(req, parameters=sw.parameters)
    ready = slap.request(dataclasses.replace(req, state="started"))
    print(
        f"{name}: {'requested' if ready else 'requested, the master is provisioning it'} ({url})"
    )
    if name != sw.instance:
        print(
            f"set instance = {name!r} in [software.{sw.name}] of {config.root / config_module.FILENAME}"
        )


@app.command
def destroy(name: str, *, yes: bool = False):
    """Destroy an instance, by re-sending its original request with state destroyed.

    Parameters
    ----------
    name
        The instance name.
    yes
        Don't ask for confirmation.
    """
    config = _config()
    existing = stored_request(_api(config), name)
    if existing is None:
        raise DevError(f"no instance {name!r} on the master")
    _confirm(f"Destroy {name} ({existing.software_release})?", yes)
    Slap.connect(config.master).request(
        dataclasses.replace(existing, state="destroyed")
    )
    print(f"{name}: destruction requested")


def _set_state(name: str, state: str) -> None:
    config = _config()
    existing = stored_request(_api(config), name)
    if existing is None:
        raise DevError(f"no instance {name!r} on the master")
    if existing.state == "destroyed":
        raise DevError(f"{name} is destroyed")
    Slap.connect(config.master).request(dataclasses.replace(existing, state=state))
    print(
        f"{name}: requested state {state}; the node acts within a minute or two (hox instances)"
    )


@app.command
def stop(name: str):
    """Stop an instance's processes and keep its data, by re-sending its original request.

    Parameters
    ----------
    name
        The instance name.
    """
    _set_state(name, "stopped")


@app.command
def start(name: str):
    """Start a stopped instance again, by re-sending its original request.

    Parameters
    ----------
    name
        The instance name.
    """
    _set_state(name, "started")


def _make_allocatable(software: str | None, pinned: str | None) -> None:
    config = _config()
    sw = config.software_named(software)
    url = _url(sw, pinned)
    api = _api(config)
    node = _node(config, api)
    panel.make_allocatable(api, config, node, sw, url)
    Slap.connect(config.master).supply(url, node.reference)
    print(f"supply of {url} on {node.reference}: requested")


@app.command
def setup(software: str | None = None):
    """One-time project setup: the web panel's work, for the branch URL.

    Software Product with the software type, branch URL as a release, Allocation
    Supply on your node with the (release, type) cell ticked and validated, branch
    URL supplied. Each step is skipped when already done.

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    """
    _make_allocatable(software, None)


@app.command
def release(commit: str, *, software: str | None = None):
    """Make a pinned version allocatable: `hox setup`'s steps for the commit's URL.

    Doesn't move the instance: `hox request --pinned COMMIT --replace` does.

    Parameters
    ----------
    commit
        The commit of the pinned URL (pushed: the node fetches it from GitHub).
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    """
    _make_allocatable(software, commit)


@app.command
def deploy(software: str | None = None) -> int:
    """From nothing on the master to an instance that passes its checks, for the branch URL.

    `hox setup`, `hox request`, then waits: for the allocation, for the node's
    build (hours for a big release without cache), for the checks (frontends
    take a while). Run it again after a failure: what is done is skipped.
    Refuses if the local branch isn't pushed, and waits until GitHub serves it.

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    """
    _require_published(_config().software_named(software))
    _make_allocatable(software, None)
    request(software)
    config = _config()
    sw = config.software_named(software)
    results = deploy_module.deploy(
        _api(config), sw, sw.branch_url, Node(config.node.ssh)
    )
    return _report_checks(results)


@app.command
def node_certificate():
    """Print your compute node's reference, certificate and key as JSON, creating the node if needed.

    The node's title is `[node] title` of hox.toml. Configure the node with
    this certificate when you install SlapOS on it. If the node
    exists, its certificate is revoked and a new one issued (the master gives
    a certificate only once): a node configured with the old one loses access.
    """
    config = _config()
    _print_json(panel.node_certificate(_api(config), config))


@app.command
def re6st_token():
    """Print the node's re6st token, to install re6st on the node with.

    It comes from a shared instance of the re6st registry ("re6st token for
    <node title>"), so asking again gives the same token. The registry takes
    10+ minutes to publish a new one. Only the token goes to stdout.
    """
    config = _config()
    print(
        panel.re6st_token(
            _api(config), Slap.connect(config.master), config, out=_stderr
        )
    )


def _stderr(line: str) -> None:
    print(line, file=sys.stderr)


@app.meta.default
def main(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    config: Path | None = None,
    debug: bool = False,
):
    """
    Parameters
    ----------
    config
        Path to hox.toml (default: the first one in the current directory or its parents).
    debug
        Show tracebacks.
    """
    global _config_path
    _config_path = config
    try:
        code = app(tokens)
    except (ConfigError, DevError, LockError, MasterError, NodeError, PortError) as e:
        if debug:
            raise
        print(f"hox: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code or 0)


def run():
    app.meta()
