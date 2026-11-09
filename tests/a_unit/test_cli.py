"""The text cli.py prints for `hox status` and `hox instances`, from fake master and node data."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import BRANCH_URL
from fakes import UP, FakeApi, FakeNode, FakeProject, unallocated

from hox_cli.cli import format_instances, format_overview, format_report
from hox_cli.config import parse
from hox_cli.node import NodeFacts, Partition, SoftwareState
from hox_cli.status import Overview, Report, instance, overview


@pytest.fixture
def config(config_data):
    return parse(config_data, Path("/work/hox"))


def test_format_report(config):
    inst = unallocated(instance(FakeApi(), "mynij-proxy-3"))
    report = Report(
        "mynij-proxy",
        inst,
        diagnosis=["d"],
        node=NodeFacts(
            [SoftwareState(BRANCH_URL, "building", "Error: x")],
            ["slappart0[default]: p"],
        ),
    )
    text = format_report(report, config)
    assert (
        "instance       mynij-proxy-3: start_requested, mynij-proxy branch, type default"
        in text
    )
    assert "partition      not allocated" in text
    assert "frontend       Mynij Proxy Frontend: not allocated" in text
    assert "promise        slappart0[default]: p" in text


def test_format_instances_shows_the_essentials(config):
    node = FakeNode(UP)
    text = format_instances(overview(config, FakeProject(), node, "COMP"))
    header, known, other = text.splitlines()
    assert header.split() == ["INSTANCE", "STATUS", "MB", "FRONTEND"]
    assert known.split() == ["mynij-proxy-3", "up", "300", "https://front.example"]
    assert other.split()[:2] == ["zzz-1", "stopped"]
    assert "s3cret" not in text
    assert node.urls == [
        BRANCH_URL
    ]  # a release being removed isn't a build to wait for


def test_format_instances_long_adds_where_it_comes_from(config):
    header, known, _ = format_instances(
        overview(config, FakeProject(), FakeNode(UP), "COMP"), long=True
    ).splitlines()
    assert header.split() == [
        "INSTANCE",
        "SOFTWARE",
        "RELEASE",
        "PARTITION",
        "STATUS",
        "MB",
        "FRONTEND",
    ]
    assert known.split()[:4] == [
        "mynij-proxy-3",
        "mynij-proxy",
        "mynij-proxy",
        "slappart1",
    ]


def test_format_overview(config):
    text = format_overview(
        overview(config, FakeProject(), FakeNode(UP), "COMP"), config
    )
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


def test_an_unreachable_node_still_lists_the_instances_as_unknown(config):
    o = overview(
        config, FakeProject(), FakeNode(error="ssh hox-node: No route to host"), "COMP"
    )
    text = format_overview(o, config)
    assert (
        "node unreachable, so what runs is unknown: ssh hox-node: No route to host"
        in text
    )
    assert text.splitlines()[-2].split()[:2] == ["mynij-proxy-3", "unknown"]
    assert " up " not in text


def test_format_overview_shortens_a_failing_promise(config):
    o = Overview(
        [],
        [],
        facts=NodeFacts(
            [],
            ["slappart4[default]: Promise 'check.py' failed with output: ERROR: long"],
        ),
    )
    assert "promise        slappart4[default]: check.py" in format_overview(o, config)


def test_format_report_names_the_services_down(config):
    report = Report(
        "mynij-proxy",
        instance(FakeApi(), "mynij-proxy-3"),
        services=Partition("slappart0", 300, 5, ["web"]),
    )
    assert "services       5 running, 300 MB, down: web" in format_report(
        report, config
    )
