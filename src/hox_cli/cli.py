"""The `hox` command."""

import dataclasses
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import httpx2
from cyclopts import App, Parameter

from . import config as config_module
from . import dev, panel
from . import status as status_module
from .config import Config, ConfigError, Software
from .dev import DevError
from .master import Api, MasterError, Request, Slap, next_name, stored_request
from .node import INSTANCE_LOG, SLAPGRID_PROMISE_FAIL, SOFTWARE_LOG, Node, NodeError

app = App(name="hox", help="Drive our SlapOS work on Rapid.Space from the terminal.")
api_app = App(name="api", help="Raw, read-only access to the master's API.")
app.command(api_app)

_config_path: Path | None = None


def _config() -> Config:
    return config_module.load(_config_path)


def _api(config: Config) -> Api:
    return Api.connect(config.master)


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
def search(query: str, *, select: tuple[str, ...] = ("title", "relative_url"), limit: int = 100):
    """Print catalog search results, e.g. 'portal_type:"Instance Tree" AND title:="mynij-proxy-3"'.

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
def status(software: str | None = None, *, json_: Annotated[bool, Parameter(name="--json")] = False):
    """Instance, frontends, builds on the node, failing promises, what GitHub serves.

    Parameters
    ----------
    software
        A [software.<name>] of hox.toml; optional when there is only one.
    json_
        Print the report as JSON.
    """
    config = _config()
    sw = config.software_named(software)
    with httpx2.Client(timeout=30) as http:
        report = status_module.gather(config, sw, _api(config), Node(config.node.ssh), http)
    if json_:
        _print_json(dataclasses.asdict(report))
    else:
        print(format_report(report, sw.branch_url))


def format_report(r: status_module.Report, branch_url: str) -> str:
    out = []
    i = r.instance
    if i is None:
        out.append("instance       none (`hox request` to create it)")
    else:
        url = "branch URL" if i.url == branch_url else i.url
        out.append(f"instance       {i.title}: {i.state}, {url}, type {i.software_type}")
        out.append(f"partition      {i.partition or 'not allocated'}")
        out += [f"diagnosis      {d}" for d in r.diagnosis]
        if i.news:
            out.append(f"news           {i.news}")
        for key, value in sorted(i.connection.items()):
            out.append(f"connection     {key} = {value}")
        for f in i.frontends:
            out.append(f"frontend       {f.title}: {'allocated' if f.allocated else 'not allocated'}")
    states = {s.url: s for s in (r.node.software if r.node else [])}
    for inst in r.installations:
        name = "branch URL" if inst.url == branch_url else inst.url
        node_state = states.get(inst.url)
        line = f"software       {name}: master {inst.state}"
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
        served = {True: "serves it", False: "serves an older file (cache: up to 5 min)", None: "could not be fetched"}
        out.append(f"github         local head {g.local_head[:9]}, {'pushed' if g.pushed else 'NOT pushed'},"
                   f" raw.githubusercontent.com {served[g.served]}")
    return "\n".join(out)


def _instance_or_fail(api: Api, sw: Software) -> status_module.Instance:
    inst = status_module.instance(api, sw.instance)
    if inst is None:
        raise DevError(f"no instance {sw.instance!r} on the master (`hox request`)")
    return inst


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
    inst = _instance_or_fail(_api(config), sw)
    return _report_checks(dev.run_checks(sw.checks, inst.connection, Node(config.node.ssh)))


def _report_checks(results: list[dev.CheckResult]) -> int:
    for r in results:
        print(f"{'ok  ' if r.ok else 'FAIL'}  {r.name}: {r.detail}" + (f"  ({r.url})" if r.url else ""))
    return 0 if all(r.ok for r in results) else 1


@app.command
def rebuild(software: str | None = None, *, software_only: bool = False, no_wait: bool = False) -> int:
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
    head = dev.require_pushed(sw)
    print(f"{sw.branch} is pushed at {head[:9]}")
    if not no_wait:
        with httpx2.Client(timeout=30) as http:
            dev.wait_for_github(sw, head, http)
    node = Node(config.node.ssh)
    node.slapgrid(f"software --force --only-sr {sw.branch_url}")
    if software_only:
        return 0
    if node.slapgrid("instance --force") == SLAPGRID_PROMISE_FAIL:
        print("some promises fail (see above)")
    inst = _instance_or_fail(_api(config), sw)
    return _report_checks(dev.run_checks(sw.checks, inst.connection, node))


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
        partition = _instance_or_fail(_api(config), sw).partition
        if partition is None:
            raise DevError(f"{sw.instance} has no partition yet")
        target = f"/srv/slapgrid/{partition}/.{partition}_*.log"
    else:
        target = SOFTWARE_LOG if kind == "software" else INSTANCE_LOG
    return Node(config.node.ssh).interactive(f"tail -n {lines}{' -F' if follow else ''} {target}")


def _url(sw: Software, pinned: str | None) -> str:
    return sw.pinned_url(dev.pushed_commit(sw, pinned)) if pinned else sw.branch_url


def _confirm(question: str, yes: bool) -> None:
    if not yes and input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise DevError("cancelled")


@app.command
def supply(software: str | None = None, *, pinned: str | None = None, remove: bool = False):
    """Supply the software release on our node (or remove it).

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
    Slap.connect(config.master).supply(url, config.node.reference, "destroyed" if remove else "available")
    print(f"{'removal' if remove else 'supply'} of {url} on {config.node.reference} requested")


@app.command
def request(
    software: str | None = None, *, name: str | None = None, pinned: str | None = None,
    replace: bool = False, yes: bool = False,
):
    """Request the instance on our node, with the branch URL or a pinned one.

    Re-requesting an existing instance re-sends its original request. The master
    refuses to change an instance's URL (HTTP 500): --replace destroys it and
    requests the next name instead (mynij-proxy-3 -> mynij-proxy-4).

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
            raise DevError(f"{name} runs {existing.software_release}; the master refuses a URL change: use --replace")
        _confirm(f"Destroy {name} and request {next_name(name)}?", yes)
        slap.request(dataclasses.replace(existing, state="destroyed"))
        print(f"{name}: destruction requested")
        name, existing = next_name(name), None
        while stored_request(api, name):
            name = next_name(name)
    req = existing or Request(url, name, sw.software_type, {"computer_guid": config.node.reference})
    ready = slap.request(dataclasses.replace(req, state="started"))
    print(f"{name}: {'requested' if ready else 'requested, the master is provisioning it'} ({url})")
    if name != sw.instance:
        print(f"set instance = {name!r} in [software.{sw.name}] of {config.root / config_module.FILENAME}")


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
    Slap.connect(config.master).request(dataclasses.replace(existing, state="destroyed"))
    print(f"{name}: destruction requested")


def _make_allocatable(software: str | None, pinned: str | None) -> None:
    config = _config()
    sw = config.software_named(software)
    url = _url(sw, pinned)
    panel.make_allocatable(_api(config), config, sw, url)
    Slap.connect(config.master).supply(url, config.node.reference)
    print(f"supply of {url} on {config.node.reference}: requested")


@app.command
def setup(software: str | None = None):
    """One-time project setup: what we did in the panel, for the branch URL.

    Software Product with the software type, branch URL as a release, Allocation
    Supply on our node with the (release, type) cell ticked and validated, branch
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
    except (ConfigError, DevError, MasterError, NodeError) as e:
        if debug:
            raise
        print(f"hox: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code or 0)


def run():
    app.meta()
