from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

# pyrefly: ignore[missing-import]
from conftest import BRANCH_URL, fixture_json

# pyrefly: ignore[missing-import]
from fakes import STALE_URL, FakeApi, FakeNode, FakeProject, unallocated

from hox_cli import cli
from hox_cli.config import ConfigError, parse
from hox_cli.node import NodeFacts, Partition, SoftwareState, System
from hox_cli.status import (
    allocation_diagnosis,
    deployed,
    gather,
    instance,
    overview,
    ticked_cells,
)

PINNED = BRANCH_URL.replace(
    "/mynij-proxy/software/", "/d797091f427246b48071b8fd042685652571c456/software/"
)


@pytest.fixture
def config(config_data):
    return parse(config_data, Path("/work/hox"))


def test_ticked_cells():
    assert ticked_cells(fixture_json("supply_line_view.json")) == {
        (PINNED, "default"),
        (BRANCH_URL, "default"),
    }


def test_instance():
    inst = instance(FakeApi(), "mynij-proxy-3")
    # pyrefly: ignore[missing-attribute]
    assert inst.partition == "slappart0"
    # pyrefly: ignore[missing-attribute]
    assert inst.connection["backend-url"] == "https://[2401:5180:12::2]:3001"
    # pyrefly: ignore[missing-attribute]
    assert inst.url == BRANCH_URL
    # pyrefly: ignore[missing-attribute]
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
        (
            {"Software Product Release Variation": []},
            "is not a release of any Software Product",
        ),
        ({"Allocation Supply": []}, "no validated Allocation Supply in PROJ-"),
        ({"Allocation Supply Line": []}, "no validated Allocation Supply ticks"),
    ],
)
def test_diagnosis_missing(config, rows, message):
    sw = config.software_named(None)
    inst = unallocated(instance(FakeApi(), sw.instance))
    [diagnosis] = allocation_diagnosis(FakeApi(**rows), config, sw, inst)
    assert message in diagnosis


def test_deployed_starts_from_the_trees(config):
    known, other = sorted(deployed(config, FakeProject()), key=lambda r: r.instance)
    assert (
        known.instance,
        known.software,
        known.release,
        known.partition,
        known.frontend,
    ) == (
        "mynij-proxy-3",
        "mynij-proxy",
        "mynij-proxy",
        "slappart1",
        "https://front.example",
    )
    assert (
        other.instance,
        other.software,
        other.release,
        other.partition,
        other.frontend,
    ) == ("zzz-1", None, STALE_URL, None, None)


def test_a_commit_shows_short_and_another_branch_in_full(config):
    sha = "d797091f427246b48071b8fd042685652571c456"
    assert cli.status_module._release(config.software["mynij-proxy"], PINNED) == sha[:9]
    url = BRANCH_URL.replace("/mynij-proxy/software/", "/hox_sbe_edge/software/")
    assert (
        cli.status_module._release(config.software["mynij-proxy"], url)
        == "hox_sbe_edge"
    )


def test_one_shot_services_exiting_is_not_a_failure(config):
    sw = dataclasses.replace(config.software["mynij-proxy"], one_shot=("sbe_initdb",))
    config.software["mynij-proxy"] = sw
    node = FakeNode([Partition("slappart1", 300, 5, ["sbe_initdb"])])
    row = overview(config, FakeProject(), node, "COMP").instances[0]
    assert (row.status, row.down) == ("up", ())
    node = FakeNode([Partition("slappart1", 300, 5, ["sbe_initdb", "web"])])
    row = overview(config, FakeProject(), node, "COMP").instances[0]
    assert (row.status, row.down) == ("1 down", ("web",))


def test_a_software_can_be_named_by_its_instance(config):
    assert config.software_named("mynij-proxy-3").name == "mynij-proxy"
    with pytest.raises(ConfigError, match="no software or instance 'nope'"):
        config.software_named("nope")


def test_status_of_one_software_leaves_out_the_others(config, monkeypatch):
    class Node:
        def facts(self, urls):
            return NodeFacts(
                [SoftwareState(u, "completed", None) for u in urls],
                ["slappart0[default]: mine", "slappart9[default]: theirs"],
            )

        def system(self):
            return System(1, [Partition("slappart0", 1, 1, [])], 1, 1, 1, 1)

    class Master(FakeApi):
        def search(self, query, select, limit=100):
            if "Software Installation" in query:
                return [
                    {"url_string": BRANCH_URL, "slap_state": "start_requested"},
                    {"url_string": STALE_URL, "slap_state": "start_requested"},
                ]
            return super().search(query, select, limit)

    monkeypatch.setattr(cli.status_module, "github", lambda sw, http: None)
    report = gather(
        # pyrefly: ignore[bad-argument-type]
        config,
        config.software["mynij-proxy"],
        Master(),
        Node(),
        None,
        "COMP",
    )
    assert [i.url for i in report.installations] == [BRANCH_URL]
    # pyrefly: ignore[missing-attribute]
    assert report.node.failing_promises == ["slappart0[default]: mine"]


def test_url_name_cuts_a_commit_and_keeps_a_branch(config):
    other_branch = BRANCH_URL.replace(
        "/mynij-proxy/software/", "/hox_sbe_edge_2026/software/"
    )
    assert config.url_name(other_branch) == "mynij-proxy @ hox_sbe_edge_2026"
    assert config.url_name(PINNED) == "mynij-proxy @ d797091f4"
