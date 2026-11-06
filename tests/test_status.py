import dataclasses
import json
import re
from pathlib import Path

import pytest
from conftest import BRANCH_URL, fixture_json

from hox_cli import cli
from hox_cli.cli import format_instances, format_overview, format_report
from hox_cli.config import ConfigError, parse
from hox_cli.master import ComputeNode
from hox_cli.node import NodeError, NodeFacts, Partition, SoftwareState, System
from hox_cli.status import (
    Overview,
    Report,
    allocation_diagnosis,
    deployed,
    gather,
    instance,
    overview,
    ticked_cells,
)

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
    text = format_report(report, config)
    assert "instance       mynij-proxy-3: start_requested, mynij-proxy branch, type default" in text
    assert "partition      not allocated" in text
    assert "frontend       Mynij Proxy Frontend: not allocated" in text
    assert "promise        slappart0[default]: p" in text


STALE_URL = BRANCH_URL.replace("/mynij-proxy/software/mynij-proxy/", "/other/software/gone/")


class FakeProject:
    """A master with one known instance, one hox.toml doesn't name, and a destroyed one's row."""

    def search(self, query, select, limit=100):
        if "Software Installation" in query:
            return [{"url_string": BRANCH_URL, "slap_state": "start_requested"},
                    {"url_string": STALE_URL, "slap_state": "destroy_requested"}]
        if "Instance Tree" in query:
            return [{"title": "zzz-1", "slap_state": "stop_requested", "url_string": STALE_URL},
                    {"title": "mynij-proxy-3", "slap_state": "start_requested", "url_string": BRANCH_URL}]
        connection = {"url": "https://front.example", "monitor-setup-url": "https://monitor.example/#password=s3cret"}
        return [
            {"title": "mynij-proxy-2", "aggregate_title": "slappart0", "getConnectionXmlAsDict": connection},
            {"title": "mynij-proxy-3", "aggregate_title": "slappart1", "getConnectionXmlAsDict": connection},
            {"title": "zzz-1", "aggregate_title": None, "getConnectionXmlAsDict": None},
        ]


class FakeNode:
    def __init__(self, partitions=None, error=None):
        self.partitions, self.error, self.urls = partitions, error, None

    def system(self):
        if self.error:
            raise NodeError(self.error)
        return System(90000, self.partitions, 3700, 7900, 45056, 122880)

    def facts(self, urls):
        self.urls = urls
        return NodeFacts([SoftwareState(u, "building", "Error: x") for u in urls], [])


UP = [Partition("slappart0", 0, 0, []), Partition("slappart1", 300, 6, [])]


def test_deployed_starts_from_the_trees(config):
    known, other = sorted(deployed(config, FakeProject()), key=lambda r: r.instance)
    assert (known.instance, known.software, known.release, known.partition, known.frontend) == (
        "mynij-proxy-3", "mynij-proxy", "mynij-proxy", "slappart1", "https://front.example")
    assert (other.instance, other.software, other.release, other.partition, other.frontend) == (
        "zzz-1", None, STALE_URL, None, None)


def test_a_commit_shows_short_and_another_branch_in_full(config):
    sha = "d797091f427246b48071b8fd042685652571c456"
    assert cli.status_module._release(config.software["mynij-proxy"], PINNED) == sha[:9]
    url = BRANCH_URL.replace("/mynij-proxy/software/", "/hox_sbe_edge/software/")
    assert cli.status_module._release(config.software["mynij-proxy"], url) == "hox_sbe_edge"


def test_format_instances_shows_the_essentials(config):
    node = FakeNode(UP)
    text = format_instances(overview(config, FakeProject(), node, "COMP"))
    header, known, other = text.splitlines()
    assert header.split() == ["INSTANCE", "STATUS", "MB", "FRONTEND"]
    assert known.split() == ["mynij-proxy-3", "up", "300", "https://front.example"]
    assert other.split()[:2] == ["zzz-1", "stopped"]
    assert "s3cret" not in text
    assert node.urls == [BRANCH_URL]  # a release being removed isn't a build to wait for


def test_format_instances_long_adds_where_it_comes_from(config):
    header, known, _ = format_instances(overview(config, FakeProject(), FakeNode(UP), "COMP"), long=True).splitlines()
    assert header.split() == ["INSTANCE", "SOFTWARE", "RELEASE", "PARTITION", "STATUS", "MB", "FRONTEND"]
    assert known.split()[:4] == ["mynij-proxy-3", "mynij-proxy", "mynij-proxy", "slappart1"]


def test_format_overview(config):
    text = format_overview(overview(config, FakeProject(), FakeNode(UP), "COMP"), config)
    assert "node           hox-node: up 1 d 1 h" in text
    assert "partitions     1 of 2 in use, free: slappart0" in text
    assert "memory         3700 MB available of 7900 MB" in text
    assert "disk           44 GB free of 120 GB" in text
    assert "builds         0 of 1 complete" in text
    assert "build          mynij-proxy branch: building (last error: Error: x)" in text
    assert "promises       all OK" in text
    assert "down  " not in text


def test_a_service_down_is_counted_in_the_table_and_named_above_it(config):
    node = FakeNode([Partition("slappart1", 300, 5, ["web", "haproxy"])])
    text = format_overview(overview(config, FakeProject(), node, "COMP"), config)
    assert "down           mynij-proxy-3: web, haproxy" in text
    assert text.splitlines()[-2].split()[:3] == ["mynij-proxy-3", "2", "down"]


def test_a_booting_partition_is_not_running_without_naming_its_services(config):
    node = FakeNode([Partition("slappart1", 0, 0, ["web", "haproxy"])])
    text = format_overview(overview(config, FakeProject(), node, "COMP"), config)
    assert "not running" in text
    assert "web" not in text


def test_one_shot_services_exiting_is_not_a_failure(config):
    sw = dataclasses.replace(config.software["mynij-proxy"], one_shot=("sbe_initdb",))
    config.software["mynij-proxy"] = sw
    node = FakeNode([Partition("slappart1", 300, 5, ["sbe_initdb"])])
    row = overview(config, FakeProject(), node, "COMP").instances[0]
    assert (row.status, row.down) == ("up", ())
    node = FakeNode([Partition("slappart1", 300, 5, ["sbe_initdb", "web"])])
    row = overview(config, FakeProject(), node, "COMP").instances[0]
    assert (row.status, row.down) == ("1 down", ("web",))


def test_an_unreachable_node_still_lists_the_instances_as_unknown(config):
    o = overview(config, FakeProject(), FakeNode(error="ssh hox-node: No route to host"), "COMP")
    text = format_overview(o, config)
    assert "node unreachable, so what runs is unknown: ssh hox-node: No route to host" in text
    assert text.splitlines()[-2].split()[:2] == ["mynij-proxy-3", "unknown"]
    assert " up " not in text


def test_a_software_can_be_named_by_its_instance(config):
    assert config.software_named("mynij-proxy-3").name == "mynij-proxy"
    with pytest.raises(ConfigError, match="no software or instance 'nope'"):
        config.software_named("nope")


def test_format_overview_shortens_a_failing_promise(config):
    o = Overview([], [], facts=NodeFacts([], ["slappart4[default]: Promise 'check.py' failed with output: ERROR: long"]))
    assert "promise        slappart4[default]: check.py" in format_overview(o, config)


def test_status_of_one_software_leaves_out_the_others(config, monkeypatch):
    class Node:
        def facts(self, urls):
            return NodeFacts([SoftwareState(u, "completed", None) for u in urls], ["slappart0[default]: mine", "slappart9[default]: theirs"])

        def system(self):
            return System(1, [Partition("slappart0", 1, 1, [])], 1, 1, 1, 1)

    class Master(FakeApi):
        def search(self, query, select, limit=100):
            if "Software Installation" in query:
                return [{"url_string": BRANCH_URL, "slap_state": "start_requested"},
                        {"url_string": STALE_URL, "slap_state": "start_requested"}]
            return super().search(query, select, limit)

    monkeypatch.setattr(cli.status_module, "github", lambda sw, http: None)
    report = gather(config, config.software["mynij-proxy"], Master(), Node(), None, "COMP")
    assert [i.url for i in report.installations] == [BRANCH_URL]
    assert report.node.failing_promises == ["slappart0[default]: mine"]


def test_format_report_names_the_services_down(config):
    report = Report("mynij-proxy", instance(FakeApi(), "mynij-proxy-3"), services=Partition("slappart0", 300, 5, ["web"]))
    assert "services       5 running, 300 MB, down: web" in format_report(report, config)


def test_status_needs_no_name_however_many_software_there_are(config_data, monkeypatch, capsys):
    other = {**config_data["software"]["mynij-proxy"], "instance": "other-1"}
    data = dict(config_data, software={**config_data["software"], "other": other})
    many = parse(data, Path("/work/hox"))
    monkeypatch.setattr(cli, "_config", lambda: many)
    monkeypatch.setattr(cli, "_api", lambda c: FakeProject())
    monkeypatch.setattr(cli, "_node", lambda c, api: ComputeNode("COMP", "compute_node_module/1"))
    monkeypatch.setattr(cli, "Node", lambda ssh: FakeNode(UP))
    cli.status()
    out = capsys.readouterr().out
    assert "node           hox-node" in out
    assert "not deployed   other" in out
    cli.instances()
    assert "mynij-proxy-3" in capsys.readouterr().out


def test_status_json_is_the_overview(config_data, monkeypatch, capsys):
    config = parse(config_data, Path("/work/hox"))
    monkeypatch.setattr(cli, "_config", lambda: config)
    monkeypatch.setattr(cli, "_api", lambda c: FakeProject())
    monkeypatch.setattr(cli, "_node", lambda c, api: ComputeNode("COMP", "compute_node_module/1"))
    monkeypatch.setattr(cli, "Node", lambda ssh: FakeNode(error="down"))
    cli.status(json_=True)
    assert json.loads(capsys.readouterr().out)["node_error"] == "down"
    cli.instances(json_=True)
    assert json.loads(capsys.readouterr().out)[0]["status"] == "unknown"


def test_url_name_cuts_a_commit_and_keeps_a_branch(config):
    other_branch = BRANCH_URL.replace("/mynij-proxy/software/", "/hox_sbe_edge_2026/software/")
    assert config.url_name(other_branch) == "mynij-proxy @ hox_sbe_edge_2026"
    assert config.url_name(PINNED) == "mynij-proxy @ d797091f4"
