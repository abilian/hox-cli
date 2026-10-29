import dataclasses
import re
from pathlib import Path

import pytest

from conftest import BRANCH_URL, fixture_json
from hox_cli.cli import format_report
from hox_cli.config import parse
from hox_cli.node import NodeFacts, SoftwareState
from hox_cli.status import Report, allocation_diagnosis, instance, ticked_cells

PINNED = BRANCH_URL.replace("/mynij-proxy/software/", "/d797091f427246b48071b8fd042685652571c456/software/")


class FakeApi:
    """Answers searches from the recorded fixtures, by portal type."""

    def __init__(self, **rows):
        self.rows = {
            "Instance Tree": fixture_json("search_tree.json")["_embedded"]["contents"],
            "Software Instance": fixture_json("search_instances.json")["_embedded"]["contents"],
            "Software Product Release Variation": fixture_json("search_release.json")["_embedded"]["contents"],
            "Allocation Supply": fixture_json("search_supply.json")["_embedded"]["contents"],
            "Allocation Supply Line": [{"relative_url": "allocation_supply_module/20261029.023-A00FDF/1"}],
        } | rows

    def search(self, query, select, limit=100):
        portal_type = re.search(r'portal_type:\(?"([^"]+)"', query).group(1)
        return [dict(r) for r in self.rows[portal_type]]

    def get(self, relative_url, view=None):
        assert (relative_url, view) == ("allocation_supply_module/20261029.023-A00FDF/1", "view")
        return fixture_json("supply_line_view.json")


@pytest.fixture
def config(config_data):
    return parse(config_data, Path("/work/hox"))


def test_ticked_cells():
    assert ticked_cells(fixture_json("supply_line_view.json")) == {(PINNED, "default"), (BRANCH_URL, "default")}


def unallocated(inst):
    return dataclasses.replace(inst, partition=None)


def test_instance():
    inst = instance(FakeApi(), "mynij-proxy-3")
    assert inst.partition == "slappart0"
    assert inst.connection["backend-url"] == "https://[2401:5180:12::2]:3001"
    assert inst.url == BRANCH_URL
    assert [(f.title, f.allocated) for f in inst.frontends] == [
        ("Mynij Proxy Frontend", False),
        ("Monitor Frontend mynij-proxy-3", False),
    ]


def test_instance_absent():
    assert instance(FakeApi(**{"Instance Tree": []}), "nope") is None


def test_diagnosis_allocatable(config):
    sw = config.software_named(None)
    inst = unallocated(instance(FakeApi(), sw.instance))
    assert allocation_diagnosis(FakeApi(), config, sw, inst) == [
        "allocatable through 'mynij-proxy on abilian-hox-node-1': wait for the master's allocation pass"
    ]


@pytest.mark.parametrize(
    "rows, message",
    [
        ({"Software Product Release Variation": []}, "is not a release of any Software Product"),
        ({"Allocation Supply": []}, "no validated Allocation Supply in PROJ-"),
        ({"Allocation Supply Line": []}, "no validated Allocation Supply ticks"),
    ],
)
def test_diagnosis_missing(config, rows, message):
    sw = config.software_named(None)
    inst = unallocated(instance(FakeApi(), sw.instance))
    [diagnosis] = allocation_diagnosis(FakeApi(**rows), config, sw, inst)
    assert message in diagnosis


def test_format_report(config):
    inst = unallocated(instance(FakeApi(), "mynij-proxy-3"))
    report = Report(
        "mynij-proxy",
        inst,
        diagnosis=["d"],
        node=NodeFacts([SoftwareState(BRANCH_URL, "building", "Error: x")], ["slappart0[default]: p"]),
    )
    text = format_report(report, BRANCH_URL)
    assert "instance       mynij-proxy-3: start_requested, branch URL, type default" in text
    assert "partition      not allocated" in text
    assert "frontend       Mynij Proxy Frontend: not allocated" in text
    assert "promise        slappart0[default]: p" in text
