import copy
import re

import pytest

from conftest import BRANCH_URL, fixture_json
from hox_cli import panel
from hox_cli.config import parse
from hox_cli.master import MasterError
from hox_cli.status import ticked_cells

PINNED = BRANCH_URL.replace("/mynij-proxy/software/", "/abc123/software/")
LINE = fixture_json("supply_line_view.json")
FIELDS = r"field_matrixbox_(allocable|variation_category_list|predicate_category_list)_cell_(\d)_0_0"


def test_matrix_fields_ticks_the_cell_and_sends_back_every_other_field():
    unticked = copy.deepcopy(LINE)
    unticked["_embedded"]["_view"]["matrixbox"]["data"][0][2][1]["value"] = 0  # branch URL row
    fields = panel.matrix_fields(unticked, (BRANCH_URL, "default"))
    assert len(fields) == 6 and all(re.fullmatch(FIELDS, k) for k in fields)
    assert fields["field_matrixbox_allocable_cell_0_0_0"] == "1"  # was ticked, stays ticked
    assert fields["field_matrixbox_allocable_cell_1_0_0"] == "1"
    assert fields["field_matrixbox_variation_category_list_cell_1_0_0"] == (
        "software_release/software_product_module/20261029.023-4ADA05/3\n"
        "software_type/software_product_module/20261029.023-4ADA05/2"
    )


def test_matrix_fields_leaves_other_unticked_cells_unticked():
    view = copy.deepcopy(LINE)
    view["_embedded"]["_view"]["matrixbox"]["data"][0][1][1]["value"] = 0  # d797 row
    assert panel.matrix_fields(view, (BRANCH_URL, "default"))["field_matrixbox_allocable_cell_0_0_0"] == "0"


def test_matrix_fields_without_the_cell():
    with pytest.raises(MasterError, match="no cell"):
        panel.matrix_fields(LINE, (PINNED, "default"))


class FakeMaster:
    """The master's catalog and forms, for what make_allocatable uses. Created
    documents show up in the catalog one search later, as with ERP5's indexing."""

    def __init__(self, existing: bool):
        self.calls, self.pending = [], []
        self.rows = {"Project": [{"relative_url": "project_module/P", "uid": 1, "validation_state": "started"}]}
        self.cells = set()
        if existing:
            self._add_everything()

    def _add_everything(self):
        self.rows |= {
            "Software Product": [self._row("software_product_module/S", 2)],
            "Software Product Type Variation": [self._row("software_product_module/S/2", 3)],
            "Software Product Release Variation": [self._row("software_product_module/S/3", 4)],
            "Allocation Supply": [self._row("allocation_supply_module/A", 5, "validated")],
            "Allocation Supply Line": [self._row("allocation_supply_module/A/1", 6)],
        }
        self.cells = {(BRANCH_URL, "default")}

    @staticmethod
    def _row(url, uid, state=None):
        return {"relative_url": url, "uid": uid, "validation_state": state}

    def search(self, query, select, limit=100):
        self.rows |= dict(self.pending)
        self.pending = []
        portal_type = re.match(r'portal_type:"([^"]+)"', query).group(1)
        return list(self.rows.get(portal_type, []))

    def _later(self, portal_type, row):
        self.pending.append((portal_type, [row]))

    def act(self, relative_url, action, fields=None):
        self.calls.append((relative_url, action, fields))
        new = {
            "add_slapos_software_product": ("Software Product", self._row("software_product_module/S", 2)),
            "add_slapos_software_type": ("Software Product Type Variation", self._row("software_product_module/S/2", 3)),
            "add_slapos_software_release": ("Software Product Release Variation", self._row("software_product_module/S/3", 4)),
        }
        if action in new:
            self._later(*new[action])
        elif action == "add_slapos_allocation_supply":
            self._later("Allocation Supply", self._row("allocation_supply_module/A", 5, "invalidated"))
            self._later("Allocation Supply Line", self._row("allocation_supply_module/A/1", 6))
        elif action == "validate_action":
            self._later("Allocation Supply", self._row("allocation_supply_module/A", 5, "validated"))
        else:
            raise AssertionError(action)

    def edit(self, relative_url, fields):
        self.calls.append((relative_url, "edit", fields))
        if "field_my_title" in fields:
            return
        assert fields == panel.matrix_fields(self.get(relative_url, "view"), (BRANCH_URL, "default"))
        self.cells.add((BRANCH_URL, "default"))

    def get(self, relative_url, view=None):
        line = copy.deepcopy(LINE)
        for row in line["_embedded"]["_view"]["matrixbox"]["data"][0][1:]:
            row[1]["value"] = int((row[0].split("/", 1)[1], "default") in self.cells)
        return line


@pytest.fixture
def config(config_data, tmp_path):
    return parse(config_data, tmp_path)


def run(master, config, url=BRANCH_URL):
    out = []
    panel.make_allocatable(master, config, config.software_named(None), url, out=out.append, sleep=lambda s: None)
    return out


def test_nothing_to_do_when_everything_exists(config):
    master = FakeMaster(existing=True)
    out = run(master, config)
    assert master.calls == []
    assert out[-2:] == ["cell 'default' x release: ticked", "supply: validated"]


def test_creates_everything_in_order(config):
    master = FakeMaster(existing=False)
    out = run(master, config)
    assert [(url, action) for url, action, _ in master.calls] == [
        ("project_module/P", "add_slapos_software_product"),
        ("software_product_module/S", "add_slapos_software_type"),
        ("software_product_module/S", "add_slapos_software_release"),
        ("project_module/P", "add_slapos_allocation_supply"),
        ("allocation_supply_module/A", "edit"),  # the title from hox.toml
        ("allocation_supply_module/A/1", "edit"),  # the matrix
        ("allocation_supply_module/A", "validate_action"),
    ]
    assert master.calls[0][2] == {"field_your_title": "mynij-proxy"}
    assert master.calls[4][2] == {"field_my_title": "mynij-proxy on abilian-hox-node-1"}
    assert out[-1] == "supply: validated now"
    assert ticked_cells(master.get("allocation_supply_module/A/1")) == {(BRANCH_URL, "default")}


def test_gives_up_when_the_catalog_never_shows_it(config, monkeypatch):
    master = FakeMaster(existing=False)
    master._later = lambda portal_type, row: None  # never indexed
    monkeypatch.setattr(panel, "INDEX_TIMEOUT_SECONDS", 10)
    with pytest.raises(MasterError, match="Software Product 'mynij-proxy': done, but the catalog doesn't show it"):
        run(master, config)
