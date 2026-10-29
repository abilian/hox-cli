"""What we did in the panel: make a release URL allocatable on our node.

Every step looks first and acts only when what it needs is missing, so running
it again is harmless. What it creates shows up in the catalog after a few
seconds (ERP5 indexes in the background): each creation waits for it.
"""

import time

from .config import Config, Software
from .master import Api, MasterError, quoted
from .status import ALLOCABLE, matrix_cells, ticked_cells

INDEX_POLL_SECONDS = 5
INDEX_TIMEOUT_SECONDS = 180


def make_allocatable(api: Api, config: Config, sw: Software, url: str, out=print, sleep=time.sleep) -> None:
    """Product, software type, release, Allocation Supply with the (url, type) cell ticked, validated."""

    def wait_for(what: str, find) -> dict:
        waited = 0
        while not (rows := find()):
            if waited >= INDEX_TIMEOUT_SECONDS:
                raise MasterError(f"{what}: done, but the catalog doesn't show it after {waited}s")
            sleep(INDEX_POLL_SECONDS)
            waited += INDEX_POLL_SECONDS
        return rows[0]

    def ensure(what: str, find, create) -> dict:
        if rows := find():
            out(f"{what}: exists")
            return rows[0]
        create()
        row = wait_for(what, find)
        out(f"{what}: created")
        return row

    project_ref, node_ref = quoted(config.project.reference), quoted(config.node.reference)
    columns = ["relative_url", "uid", "validation_state"]
    projects = api.search(f'portal_type:"Project" AND reference:={project_ref}', columns)
    if not projects:
        raise MasterError(f"no project {config.project.reference} visible with this certificate")
    project = projects[0]

    product = ensure(
        f"Software Product {sw.product!r}",
        lambda: api.search(
            f'portal_type:"Software Product" AND follow_up_reference:={project_ref}'
            f' AND title:={quoted(sw.product)} AND validation_state:!="invalidated"', columns),
        lambda: api.act(project["relative_url"], "add_slapos_software_product", {"field_your_title": sw.product}),
    )
    ensure(
        f"software type {sw.software_type!r}",
        lambda: api.search(
            f'portal_type:"Software Product Type Variation" AND parent_uid:{product["uid"]}'
            f" AND title:={quoted(sw.software_type)}", columns),
        lambda: api.act(product["relative_url"], "add_slapos_software_type",
                        {"field_your_software_type": sw.software_type}),
    )
    ensure(
        f"release {url}",
        lambda: api.search(
            f'portal_type:"Software Product Release Variation" AND parent_uid:{product["uid"]}'
            f" AND url_string:={quoted(url)}", columns),
        lambda: api.act(product["relative_url"], "add_slapos_software_release", {"field_your_software_release": url}),
    )

    def find_supplies(title: str | None = sw.supply_title) -> list[dict]:
        rows = api.search(
            f'portal_type:"Allocation Supply" AND destination_project_reference:={project_ref}'
            f" AND aggregate_reference:={node_ref}" + (f" AND title:={quoted(title)}" if title else ""), columns)
        return sorted(rows, key=lambda r: r["validation_state"] != "validated")

    def create_supply() -> None:
        # The panel's "Add Allocation Supply" makes an invalidated supply on all the
        # project's open nodes, titled "All compute nodes <date>", with one line per product.
        before = {r["relative_url"] for r in find_supplies(None)}
        api.act(project["relative_url"], "add_slapos_allocation_supply")
        new = wait_for("new Allocation Supply", lambda: [r for r in find_supplies(None) if r["relative_url"] not in before])
        api.edit(new["relative_url"], {"field_my_title": sw.supply_title})

    supply = ensure(f"Allocation Supply {sw.supply_title!r}", find_supplies, create_supply)
    line = ensure(
        f"its line for {sw.product!r}",
        lambda: api.search(
            f'portal_type:"Allocation Supply Line" AND parent_uid:{supply["uid"]}'
            f" AND resource_title:={quoted(sw.product)}", columns),
        # Invalidating a supply adds the lines of the products it lacks; validated again below.
        lambda: api.act(supply["relative_url"], "slapos_panel_workflow_fast_invalidate"),
    )

    cell = (url, sw.software_type)
    if cell in ticked_cells(api.get(line["relative_url"], "view")):
        out(f"cell {sw.software_type!r} x release: ticked")
    else:
        api.edit(line["relative_url"], matrix_fields(api.get(line["relative_url"], "view"), cell))
        if cell not in ticked_cells(api.get(line["relative_url"], "view")):
            raise MasterError(f"{line['relative_url']}: the cell for {cell} is still not ticked after the edit")
        out(f"cell {sw.software_type!r} x release: ticked now")

    if find_supplies()[0]["validation_state"] == "validated":
        out("supply: validated")
    else:
        api.act(supply["relative_url"], "validate_action")
        wait_for("supply validation", lambda: [r for r in find_supplies() if r["validation_state"] == "validated"])
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
            fields["field_" + field["key"]] = "1" if field["value"] or (release, software_type) == cell else "0"
        else:
            fields["field_" + field["key"]] = "\n".join(field["value"])
    return fields
