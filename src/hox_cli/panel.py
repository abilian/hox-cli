"""The web panel's work: a new node's certificate and re6st token, and making a release URL allocatable on it.

Every step looks first and acts only when what it needs is missing, so running
it again is harmless. What it creates shows up in the catalog after a few
seconds (ERP5 indexes in the background): each creation waits for it.
"""

import time

from .config import Config, Software
from .dev import wait_for
from .master import Api, ComputeNode, MasterError, Request, Slap, nodes_titled, quoted
from .status import ALLOCABLE, matrix_cells, ticked_cells

RE6ST_POLL_SECONDS = 30
RE6ST_TIMEOUT_SECONDS = 45 * 60  # the registry takes 10+ minutes
COLUMNS = ["relative_url", "uid", "validation_state"]


def _project(api: Api, config: Config) -> dict:
    rows = api.search(
        f'portal_type:"Project" AND reference:={quoted(config.project.reference)}',
        COLUMNS,
    )
    if not rows:
        raise MasterError(
            f"no project {config.project.reference} visible with this certificate"
        )
    return rows[0]


def node_certificate(api: Api, config: Config) -> dict[str, str]:
    """The compute node's reference, certificate and key, to configure it with.

    What `slapos node register` gets with a token, without the token: the
    panel's "Add Compute Node Token" works only through panel.rapid.space,
    which doesn't take a client certificate. Creates the node (panel: "Add Compute
    Node") if none has `[node] title`. Otherwise revokes its certificate and
    requests a new one, since the master gives a certificate only once: a node
    still configured with the old one loses access.
    """
    nodes = nodes_titled(api, config.project.reference, config.node.title)
    if len(nodes) > 1:
        raise MasterError(f"compute node {config.node.title!r}: {len(nodes)} of them")
    if nodes:
        api.act(nodes[0].relative_url, "revoke_slapos_certificate")
        answer = api.act(nodes[0].relative_url, "request_slapos_certificate_action")
    else:
        answer = api.act(
            _project(api, config)["relative_url"],
            "add_slapos_compute_node",
            {"field_your_title": config.node.title},
        )
    if answer is None:
        raise MasterError(
            "the master answered with a redirect, not with the node's certificate"
        )
    # The script answers with a form whose fields hold its results.
    names = {
        "reference": "your_compute_node_reference",
        "certificate": "your_compute_node_certificate",
        "key": "your_compute_node_key",
    }
    missing = [n for n in names.values() if n not in answer]
    if missing:
        raise MasterError(
            f"no {', '.join(missing)} in the master's answer; it has: {', '.join(answer)}"
        )
    return {name: answer[field]["default"] for name, field in names.items()}


def re6st_token(
    api: Api, slap: Slap, config: Config, out=print, sleep=time.sleep
) -> str:
    """The node's re6st token: a shared instance of the re6st registry, in its own project.

    Requesting it again returns the same instance, hence the same token.
    """
    title = f"re6st token for {config.node.title}"
    slap.request(
        Request(
            config.node.re6st_release,
            title,
            "default",
            {},
            shared=True,
            project=config.node.re6st_project,
        )
    )

    def find() -> list[str]:
        rows = api.search(
            f'portal_type:"Slave Instance" AND validation_state:validated AND title:={quoted(title)}',
            ["getConnectionXmlAsDict"],
        )
        if (
            not rows or not rows[0]["getConnectionXmlAsDict"]
        ):  # None until the registry publishes
            return []
        token = rows[0]["getConnectionXmlAsDict"].get("token")
        return [token] if token else []

    out(f"{title}: requested, waiting for the registry to publish it (10+ minutes)")
    return wait_for(title, find, sleep, RE6ST_POLL_SECONDS, RE6ST_TIMEOUT_SECONDS)


def open_for_allocation(api: Api, node: ComputeNode, out=print) -> None:
    """A new node's allocation scope is closed: nothing is allocated on it until it is open."""

    def scope() -> str:
        return api.get(node.relative_url, "slapos_panel_view")["_embedded"]["_view"][
            "my_allocation_scope"
        ]["default"]

    if scope() == "open":
        out("node allocation scope: open")
        return
    api.edit(
        node.relative_url,
        {"field_my_allocation_scope": "open"},
        view="slapos_panel_view",
    )
    if scope() != "open":
        raise MasterError(
            f"{node.relative_url}: allocation scope is still {scope()!r} after the edit"
        )
    out("node allocation scope: open now")


def make_allocatable(
    api: Api,
    config: Config,
    node: ComputeNode,
    sw: Software,
    url: str,
    out=print,
    sleep=time.sleep,
) -> None:
    """Node open, product, software type, release, Allocation Supply with the (url, type) cell ticked, validated."""

    def ensure(what: str, find, create) -> dict:
        if rows := find():
            out(f"{what}: exists")
            return rows[0]
        create()
        row = wait_for(what, find, sleep)
        out(f"{what}: created")
        return row

    open_for_allocation(api, node, out)
    project_ref, node_ref = quoted(config.project.reference), quoted(node.reference)
    project = _project(api, config)
    supply_title = f"{sw.product} on {config.node.title}"

    product = ensure(
        f"Software Product {sw.product!r}",
        lambda: api.search(
            f'portal_type:"Software Product" AND follow_up_reference:={project_ref}'
            f' AND title:={quoted(sw.product)} AND validation_state:!="invalidated"',
            COLUMNS,
        ),
        lambda: api.act(
            project["relative_url"],
            "add_slapos_software_product",
            {"field_your_title": sw.product},
        ),
    )
    ensure(
        f"software type {sw.software_type!r}",
        lambda: api.search(
            f'portal_type:"Software Product Type Variation" AND parent_uid:{product["uid"]}'
            f" AND title:={quoted(sw.software_type)}",
            COLUMNS,
        ),
        lambda: api.act(
            product["relative_url"],
            "add_slapos_software_type",
            {"field_your_software_type": sw.software_type},
        ),
    )
    ensure(
        f"release {url}",
        lambda: api.search(
            f'portal_type:"Software Product Release Variation" AND parent_uid:{product["uid"]}'
            f" AND url_string:={quoted(url)}",
            COLUMNS,
        ),
        lambda: api.act(
            product["relative_url"],
            "add_slapos_software_release",
            {"field_your_software_release": url},
        ),
    )

    def find_supplies(title: str | None = supply_title) -> list[dict]:
        rows = api.search(
            f'portal_type:"Allocation Supply" AND destination_project_reference:={project_ref}'
            f" AND aggregate_reference:={node_ref}"
            + (f" AND title:={quoted(title)}" if title else ""),
            COLUMNS,
        )
        return sorted(rows, key=lambda r: r["validation_state"] != "validated")

    def create_supply() -> None:
        # The panel's "Add Allocation Supply" makes an invalidated supply on all the
        # project's open nodes, titled "All compute nodes <date>", with one line per product.
        before = {r["relative_url"] for r in find_supplies(None)}
        api.act(project["relative_url"], "add_slapos_allocation_supply")
        new = wait_for(
            "new Allocation Supply",
            lambda: [r for r in find_supplies(None) if r["relative_url"] not in before],
            sleep,
        )
        api.edit(new["relative_url"], {"field_my_title": supply_title})

    supply = ensure(f"Allocation Supply {supply_title!r}", find_supplies, create_supply)
    line = ensure(
        f"its line for {sw.product!r}",
        lambda: api.search(
            f'portal_type:"Allocation Supply Line" AND parent_uid:{supply["uid"]}'
            f" AND resource_title:={quoted(sw.product)}",
            COLUMNS,
        ),
        # Invalidating a supply adds the lines of the products it lacks; validated again below.
        lambda: api.act(
            supply["relative_url"], "slapos_panel_workflow_fast_invalidate"
        ),
    )

    cell = (url, sw.software_type)
    if cell in ticked_cells(api.get(line["relative_url"], "view")):
        out(f"cell {sw.software_type!r} x release: ticked")
    else:
        api.edit(
            line["relative_url"],
            matrix_fields(api.get(line["relative_url"], "view"), cell),
        )
        if cell not in ticked_cells(api.get(line["relative_url"], "view")):
            raise MasterError(
                f"{line['relative_url']}: the cell for {cell} is still not ticked after the edit"
            )
        out(f"cell {sw.software_type!r} x release: ticked now")

    if find_supplies()[0]["validation_state"] == "validated":
        out("supply: validated")
    else:
        api.act(supply["relative_url"], "validate_action")
        wait_for(
            "supply validation",
            lambda: [
                r for r in find_supplies() if r["validation_state"] == "validated"
            ],
            sleep,
        )
        out("supply: validated now")


def matrix_fields(line_view: dict, cell: tuple[str, str]) -> dict[str, str]:
    """Every field of the line's matrix as Base_edit expects them back, with `cell` ticked.

    The matrix is validated as a whole: if one of its fields is missing from the
    POST, Base_edit skips all of them.
    """
    cells = list(matrix_cells(line_view))
    if not any((r, t) == cell for r, t, _ in cells):
        raise MasterError(f"the supply line's matrix has no cell for {cell}")
    fields = {}
    for release, software_type, field in cells:
        if field["field_id"] == ALLOCABLE:
            fields["field_" + field["key"]] = (
                "1" if field["value"] or (release, software_type) == cell else "0"
            )
        else:
            fields["field_" + field["key"]] = "\n".join(field["value"])
    return fields
