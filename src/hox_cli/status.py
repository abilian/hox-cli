"""`hox status`: what the master, the node and GitHub say, about one software or the whole system."""

import dataclasses
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx2

from .config import Config, Software, short_ref
from .master import Api, quoted
from .node import Node, NodeError, NodeFacts, Partition, System, git


@dataclass(frozen=True)
class Frontend:
    title: str
    allocated: bool


@dataclass(frozen=True)
class Instance:
    title: str
    state: str
    url: str
    software_type: str
    partition: str | None
    connection: dict[str, str]
    news: str | None
    frontends: list[Frontend]


@dataclass(frozen=True)
class Installation:
    url: str
    state: str


@dataclass(frozen=True)
class GitHub:
    local_head: str
    pushed: bool  # origin/<branch> (as last fetched) is at the local head
    served: (
        bool | None
    )  # raw.githubusercontent.com serves software.cfg as at the local head; None: fetch failed


@dataclass
class Report:
    software: str
    instance: Instance | None
    diagnosis: list[str] = field(default_factory=list)
    installations: list[Installation] = field(default_factory=list)
    node: NodeFacts | None = None
    services: Partition | None = None
    node_error: str | None = None
    github: GitHub | None = None


@dataclass(frozen=True)
class Deployed:
    """An instance tree of the project. `state` is what was requested: only the node knows what runs."""

    instance: str
    software: str | None  # its [software.<name>] of hox.toml
    release: str  # the branch or commit of a release hox.toml knows, else its URL
    state: str
    partition: str | None
    frontend: str | None
    status: str = "unknown"
    down: tuple[str, ...] = ()
    memory_mb: int | None = None


@dataclass
class Overview:
    instances: list[Deployed]
    undeployed: list[str]  # software of hox.toml with no instance
    system: System | None = None
    facts: NodeFacts | None = None
    node_error: str | None = None


def overview(config: Config, api: Api, node: Node, node_reference: str) -> Overview:
    rows = deployed(config, api)
    result = Overview(rows, sorted(set(config.software) - {r.software for r in rows}))
    known = {sw.name: sw for sw in config.software.values()}
    wanted = [
        i.url
        for i in installations(api, node_reference)
        if i.state == "start_requested"
    ]
    try:
        result.system = node.system()
        result.facts = node.facts(wanted)
    except NodeError as e:
        result.node_error = str(e)
    result.instances = [
        _observed(r, known.get(r.software), result.system) for r in rows
    ]
    return result


def _observed(row: Deployed, sw: Software | None, system: System | None) -> Deployed:
    """The row, with what runs as far as we can tell: the master only knows what was requested."""
    if row.state != "start_requested":
        status = {"stop_requested": "stopped", "destroy_requested": "being destroyed"}[
            row.state
        ]
        return dataclasses.replace(row, status=status)
    if row.partition is None:
        return dataclasses.replace(row, status="not allocated")
    if system is None:
        return row
    partition = _services(system, row.partition, sw)
    if partition is None or not (partition.running or partition.down):
        return dataclasses.replace(row, status="no services")
    down = tuple(partition.down)
    status = (
        "not running"
        if not partition.running
        else f"{len(down)} down"
        if down
        else "up"
    )
    return dataclasses.replace(
        row, status=status, down=down, memory_mb=partition.memory_mb
    )


def _services(system: System, name: str, sw: Software | None) -> Partition | None:
    """The node's partition, leaving out of `down` the software's one-shot services."""
    partition = next((p for p in system.partitions if p.name == name), None)
    if partition is None or sw is None:
        return partition
    return dataclasses.replace(
        partition, down=[d for d in partition.down if d not in sw.one_shot]
    )


# The master keeps a destroyed instance's rows: more than the project's partitions.
LIST_LIMIT = 1000


def deployed(config: Config, api: Api) -> list[Deployed]:
    project = f"validation_state:validated AND follow_up_reference:={quoted(config.project.reference)}"
    trees = api.search(
        f'portal_type:"Instance Tree" AND {project}',
        ["title", "slap_state", "url_string"],
        LIST_LIMIT,
    )
    # Only `url` of the connection parameters: others can hold credentials (monitor-setup-url).
    roots = {
        r["title"]: r
        for r in api.search(
            f'portal_type:"Software Instance" AND {project}',
            ["title", "aggregate_title", "getConnectionXmlAsDict"],
            LIST_LIMIT,
        )
    }
    known = {sw.instance: sw for sw in config.software.values()}
    rows = []
    for tree in sorted(trees, key=lambda t: t["title"]):
        title, sw = tree["title"], known.get(tree["title"])
        root = roots.get(title, {})
        rows.append(
            Deployed(
                instance=title,
                software=sw.name if sw else None,
                release=_release(sw, tree["url_string"]),
                state=tree["slap_state"],
                partition=root.get("aggregate_title"),
                frontend=(root.get("getConnectionXmlAsDict") or {}).get("url"),
            )
        )
    return rows


def _release(sw: Software | None, url: str) -> str:
    ref = sw.ref_of(url) if sw else None
    return url if ref is None else short_ref(ref)


def gather(
    config: Config,
    sw: Software,
    api: Api,
    node: Node,
    http: httpx2.Client,
    node_reference: str,
) -> Report:
    report = Report(sw.name, instance(api, sw.instance))
    if report.instance and report.instance.partition is None:
        report.diagnosis = allocation_diagnosis(api, config, sw, report.instance)
    report.installations = [
        i for i in installations(api, node_reference) if sw.ref_of(i.url) is not None
    ]
    try:
        report.node = node.facts([i.url for i in report.installations])
        if report.instance and (partition := report.instance.partition):
            report.services = _services(node.system(), partition, sw)
            promises = [
                p for p in report.node.failing_promises if p.startswith(f"{partition}[")
            ]
            report.node = dataclasses.replace(report.node, failing_promises=promises)
    except NodeError as e:
        report.node_error = str(e)
    report.github = github(sw, http)
    return report


def instance(api: Api, title: str) -> Instance | None:
    trees = api.search(
        f'portal_type:"Instance Tree" AND validation_state:validated AND title:={quoted(title)}',
        ["title", "relative_url", "slap_state", "url_string", "source_reference"],
    )
    if not trees:
        return None
    tree = trees[0]
    rows = api.search(
        'portal_type:("Software Instance","Slave Instance") AND validation_state:validated'
        f" AND default_specialise_title:={quoted(title)}",
        [
            "title",
            "portal_type",
            "slap_state",
            "aggregate_title",
            "getConnectionXmlAsDict",
            "SoftwareInstance_getNewsDict",
        ],
    )
    root = next(
        (
            r
            for r in rows
            if r["portal_type"] == "Software Instance" and r["title"] == title
        ),
        None,
    )
    frontends = [
        Frontend(r["title"], r["aggregate_title"] is not None)
        for r in rows
        if r["portal_type"] == "Slave Instance"
    ]
    news = (root or {}).get("SoftwareInstance_getNewsDict") or {}
    return Instance(
        title=title,
        state=tree["slap_state"],
        url=tree["url_string"],
        software_type=tree["source_reference"],
        partition=(root or {}).get("aggregate_title"),
        connection=(root or {}).get("getConnectionXmlAsDict") or {},
        news=news.get("text") if not news.get("no_data") else None,
        frontends=frontends,
    )


def installations(api: Api, node_reference: str) -> list[Installation]:
    rows = api.search(
        'portal_type:"Software Installation" AND validation_state:validated'
        f" AND default_aggregate_reference:={quoted(node_reference)}",
        ["url_string", "slap_state"],
    )
    return [Installation(r["url_string"], r["slap_state"]) for r in rows]


def allocation_diagnosis(
    api: Api, config: Config, sw: Software, inst: Instance
) -> list[str]:
    """Why an instance has no partition: what the project lacks for its URL and type."""
    if inst.software_type != sw.software_type:
        return [
            f"instance software type is {inst.software_type!r}, config says {sw.software_type!r}"
        ]
    releases = api.search(
        f'portal_type:"Software Product Release Variation" AND url_string:={quoted(inst.url)}',
        ["relative_url"],
    )
    if not releases:
        return [
            f"{inst.url} is not a release of any Software Product: `hox setup` or `hox release`"
        ]
    supplies = api.search(
        'portal_type:"Allocation Supply" AND validation_state:validated'
        f" AND destination_project_reference:={quoted(config.project.reference)}",
        ["relative_url", "title"],
    )
    if not supplies:
        return [f"no validated Allocation Supply in {config.project.reference}"]
    lines = api.search(
        f'portal_type:"Allocation Supply Line" AND resource_title:={quoted(sw.product)}',
        ["relative_url"],
    )
    for supply in supplies:
        for line in lines:
            if line["relative_url"].startswith(supply["relative_url"] + "/"):
                if (inst.url, inst.software_type) in ticked_cells(
                    api.get(line["relative_url"], "view")
                ):
                    return [
                        f"allocatable through {supply['title']!r}: wait for the master's allocation pass"
                    ]
    return [f"no validated Allocation Supply ticks ({inst.url}, {inst.software_type})"]


_CELL = re.compile(r"_cell_\d+_(\d+)_\d+$")
ALLOCABLE = "matrixbox_allocable"


def matrix_cells(line_view: dict) -> Iterator[tuple[str, str, dict]]:
    """(release URL, software type, field) for each field of an Allocation Supply Line's matrix.

    The matrix is a list of tables (one per value of a third dimension, unused
    here), each a header row of software types and one row per release. A cell
    has three fields: the `allocable` checkbox and two hidden category lists.
    """
    for table in line_view["_embedded"]["_view"]["matrixbox"]["data"]:
        header, rows = table[0], table[1:]
        for row in rows:
            for field in row[1:]:
                column = int(_CELL.search(field["key"]).group(1))
                yield _label(row[0]), _label(header[1 + column]), field


def ticked_cells(line_view: dict) -> set[tuple[str, str]]:
    """(release URL, software type) pairs ticked in an Allocation Supply Line's matrix."""
    return {
        (r, t)
        for r, t, f in matrix_cells(line_view)
        if f["field_id"] == ALLOCABLE and f["value"]
    }


def _label(label: str) -> str:
    # "Software Release/https://…" -> "https://…"
    return label.split("/", 1)[1]


def github(sw: Software, http: httpx2.Client) -> GitHub:
    head = git(sw.checkout, "rev-parse", sw.branch).strip()
    remote = git(sw.checkout, "rev-parse", f"origin/{sw.branch}").strip()
    expected = git(sw.checkout, "show", f"{head}:{sw.path}")
    try:
        response = http.get(sw.branch_url)
        served = response.status_code == 200 and response.text == expected
    except httpx2.TransportError:
        served = None
    return GitHub(head, head == remote, served)
