from __future__ import annotations

import dataclasses

import pytest

# pyrefly: ignore[missing-import]
from conftest import BRANCH_URL

from hox_cli import deploy, status
from hox_cli.config import Check, parse
from hox_cli.dev import DevError
from hox_cli.node import NodeFacts, SoftwareState


@pytest.fixture
def sw(config_data, tmp_path):
    return parse(config_data, tmp_path).software_named(None)


def instance(partition=None, connection=None):
    return status.Instance(
        "mynij-proxy-3",
        "start_requested",
        BRANCH_URL,
        "default",
        partition,
        connection or {},
        None,
        [],
    )


def test_wait_allocated(sw, monkeypatch):
    answers = iter([None, instance(), instance("slappart3")])
    monkeypatch.setattr(status, "instance", lambda api, title: next(answers))
    out = []
    assert (
        # pyrefly: ignore[bad-argument-type]
        deploy.wait_allocated(None, sw, out.append, sleep=lambda s: None) == "slappart3"
    )
    assert out[-1] == "mynij-proxy-3: allocated on slappart3"


class FakeNode:
    def __init__(self, states):
        self.states = iter(states)

    def facts(self, urls):
        state, error = next(self.states)
        return NodeFacts([SoftwareState(urls[0], state, error)], [])


def test_wait_built_reports_each_change_once():
    node = FakeNode([
        ("absent", None),
        ("building", None),
        ("building", None),
        ("building", "Error: boom"),
        ("completed", None),
    ])
    out = []
    # pyrefly: ignore[bad-argument-type]
    deploy.wait_built(node, BRANCH_URL, out.append, sleep=lambda s: None)
    assert [line.split(": ", 1)[1] for line in out] == [
        "absent",
        "building",
        "building (last error: Error: boom)",
        "completed",
    ]


def test_wait_built_gives_up(monkeypatch):
    monkeypatch.setattr(deploy, "BUILD_TIMEOUT_SECONDS", 120)
    with pytest.raises(DevError, match="build of .*: still not there after 2 min"):
        deploy.wait_built(
            # pyrefly: ignore[bad-argument-type]
            FakeNode([("building", None)] * 10),
            BRANCH_URL,
            lambda _: None,
            sleep=lambda s: None,
        )


def test_wait_checks_until_the_frontend_is_published(sw, monkeypatch):
    sw = dataclasses.replace(
        sw, checks=(Check("frontend", "{url}/ping", expect_status=200),)
    )
    answers = iter([
        instance("slappart3"),
        instance("slappart3", {"url": "https://f.example"}),
    ])
    monkeypatch.setattr(status, "instance", lambda api, title: next(answers))

    class Node:
        def run(self, command):
            return "HTTP/1.1 200 OK\r\n\r\ncurl-exit=0\n"

    out = []
    # pyrefly: ignore[bad-argument-type]
    [result] = deploy.wait_checks(None, sw, Node(), out.append, sleep=lambda s: None)
    assert result.ok
    assert out == ["waiting for: frontend: url is not published yet"]


def test_deploy_runs_only_our_partition(sw, monkeypatch):
    monkeypatch.setattr(
        deploy, "wait_allocated", lambda api, sw, out, sleep: "slappart8"
    )
    monkeypatch.setattr(deploy, "wait_built", lambda node, url, out, sleep: None)
    monkeypatch.setattr(deploy, "wait_checks", lambda api, sw, node, out, sleep: [])

    class Node:
        def slapgrid(self, args, out):
            self.args = args
            return 0

    node = Node()
    # pyrefly: ignore[bad-argument-type]
    deploy.deploy(None, sw, BRANCH_URL, node, out=lambda _: None)
    assert node.args == "instance --force --only-cp slappart8"
