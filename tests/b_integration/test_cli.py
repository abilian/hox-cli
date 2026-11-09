"""Command functions of cli.py, run through config, status and master, with a fake master and node."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from conftest import BRANCH_URL
from fakes import UP, FakeNode, FakeProject

from hox_cli import cli
from hox_cli.config import parse
from hox_cli.dev import DevError
from hox_cli.master import ComputeNode, Request

PINNED = BRANCH_URL.replace("/mynij-proxy/software/", "/abc123/software/")


class FakeSlap:
    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return True


@pytest.fixture
def master(config_data, monkeypatch):
    """Instances on the fake master: name -> Request. Returns (instances, slap)."""
    config = parse(config_data, Path("/work/hox"))
    instances = {}
    slap = FakeSlap()
    monkeypatch.setattr(cli, "_config", lambda: config)
    monkeypatch.setattr(cli, "_api", lambda c: None)
    monkeypatch.setattr(
        cli,
        "_node",
        lambda c, api: ComputeNode("COMP-20261027.021-9D1365", "compute_node_module/1"),
    )
    monkeypatch.setattr(cli.Slap, "connect", lambda m: slap)
    monkeypatch.setattr(cli, "stored_request", lambda api, name: instances.get(name))
    monkeypatch.setattr(cli.dev, "pushed_commit", lambda sw, commit: commit)
    return instances, slap


def existing(url, name="mynij-proxy-3"):
    return Request(
        url, name, "default", {"computer_guid": "COMP-20261027.021-9D1365"}, {"p": "1"}
    )


def test_new_instance_is_requested_on_our_node(master):
    instances, slap = master
    cli.request()
    [req] = slap.requests
    assert (req.reference, req.software_release, req.state) == (
        "mynij-proxy-3",
        BRANCH_URL,
        "started",
    )
    assert req.filter == {"computer_guid": "COMP-20261027.021-9D1365"}


def test_existing_instance_gets_its_original_request_again(master):
    instances, slap = master
    instances["mynij-proxy-3"] = existing(BRANCH_URL)
    cli.request()
    assert slap.requests == [existing(BRANCH_URL)]  # parameters and filter included


def test_parameters_in_hox_toml_replace_the_instances(master, config_data):
    instances, slap = master
    instances["mynij-proxy-3"] = existing(BRANCH_URL)
    config = cli._config()
    sw = dataclasses.replace(config.software["mynij-proxy"], parameters={"q": "2"})
    config.software["mynij-proxy"] = sw
    cli.request()
    [req] = slap.requests
    assert req.parameters == {"q": "2"}
    assert req.filter == existing(BRANCH_URL).filter


def test_url_change_is_refused_without_replace(master):
    instances, slap = master
    instances["mynij-proxy-3"] = existing(BRANCH_URL)
    with pytest.raises(DevError, match="use --replace"):
        cli.request(pinned="abc123")
    assert slap.requests == []


def test_replace_destroys_and_requests_the_next_free_name(master, capsys):
    instances, slap = master
    instances["mynij-proxy-3"] = existing(BRANCH_URL)
    instances["mynij-proxy-4"] = existing(BRANCH_URL, "mynij-proxy-4")
    cli.request(pinned="abc123", replace=True, yes=True)
    destroyed, created = slap.requests
    assert (destroyed.reference, destroyed.state, destroyed.software_release) == (
        "mynij-proxy-3",
        "destroyed",
        BRANCH_URL,
    )
    assert (created.reference, created.state, created.software_release) == (
        "mynij-proxy-5",
        "started",
        PINNED,
    )
    assert "set instance = 'mynij-proxy-5'" in capsys.readouterr().out


def test_replace_asks_first(master, monkeypatch):
    instances, slap = master
    instances["mynij-proxy-3"] = existing(BRANCH_URL)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(DevError, match="cancelled"):
        cli.request(pinned="abc123", replace=True)
    assert slap.requests == []


def test_destroy_resends_the_original_request(master):
    instances, slap = master
    instances["old"] = existing(BRANCH_URL, "old")
    cli.destroy("old", yes=True)
    assert slap.requests == [
        dataclasses.replace(existing(BRANCH_URL, "old"), state="destroyed")
    ]


@pytest.mark.parametrize(
    "command, state", [(cli.stop, "stopped"), (cli.start, "started")]
)
def test_stop_and_start_resend_the_original_request(master, command, state):
    instances, slap = master
    instances["old"] = existing(BRANCH_URL, "old")
    command("old")
    assert slap.requests == [
        dataclasses.replace(existing(BRANCH_URL, "old"), state=state)
    ]


def test_stop_unknown(master):
    with pytest.raises(DevError, match="no instance 'x'"):
        cli.stop("x")


def test_start_refuses_a_destroyed_instance(master):
    instances, slap = master
    instances["old"] = dataclasses.replace(
        existing(BRANCH_URL, "old"), state="destroyed"
    )
    with pytest.raises(DevError, match="old is destroyed"):
        cli.start("old")
    assert slap.requests == []


def test_destroy_unknown(master):
    with pytest.raises(DevError, match="no instance 'x'"):
        cli.destroy("x", yes=True)


def test_deploy_refuses_an_unpushed_branch_before_touching_the_master(
    master, monkeypatch
):
    _, slap = master

    def unpushed(sw):
        raise DevError(f"{sw.branch}: push it first")

    def touched(*args):
        raise AssertionError("the master was touched")

    monkeypatch.setattr(cli.dev, "require_pushed", unpushed)
    monkeypatch.setattr(cli, "_make_allocatable", touched)
    with pytest.raises(DevError, match="push it first"):
        cli.deploy()
    assert slap.requests == []


def test_status_needs_no_name_however_many_software_there_are(
    config_data, monkeypatch, capsys
):
    other = {**config_data["software"]["mynij-proxy"], "instance": "other-1"}
    data = dict(config_data, software={**config_data["software"], "other": other})
    many = parse(data, Path("/work/hox"))
    monkeypatch.setattr(cli, "_config", lambda: many)
    monkeypatch.setattr(cli, "_api", lambda c: FakeProject())
    monkeypatch.setattr(
        cli, "_node", lambda c, api: ComputeNode("COMP", "compute_node_module/1")
    )
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
    monkeypatch.setattr(
        cli, "_node", lambda c, api: ComputeNode("COMP", "compute_node_module/1")
    )
    monkeypatch.setattr(cli, "Node", lambda ssh: FakeNode(error="down"))
    cli.status(json_=True)
    assert json.loads(capsys.readouterr().out)["node_error"] == "down"
    cli.instances(json_=True)
    assert json.loads(capsys.readouterr().out)[0]["status"] == "unknown"
