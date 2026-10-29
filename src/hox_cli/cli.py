"""The `hox` command."""

import dataclasses
import json
import sys
from pathlib import Path
from typing import Annotated

import httpx2
from cyclopts import App, Parameter

from . import config as config_module
from . import status as status_module
from .config import Config, ConfigError
from .master import Api, MasterError
from .node import Node, NodeError

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
        app(tokens)
    except (ConfigError, MasterError, NodeError) as e:
        if debug:
            raise
        print(f"hox: {e}", file=sys.stderr)
        sys.exit(1)


def run():
    app.meta()
