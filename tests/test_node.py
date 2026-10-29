import subprocess

import pytest

from conftest import fixture_text
from hox_cli.node import (
    SEPARATOR,
    Node,
    NodeError,
    failing_promises,
    last_buildout_errors,
    parse_facts,
    software_dir,
)

PINNED = "https://raw.githubusercontent.com/abilian/slapos_abilian_edge/d797091f427246b48071b8fd042685652571c456/software/mynij-proxy/software.cfg"


def test_software_dir_is_md5_of_url():
    # The node built this URL in /opt/slapgrid/d09513eb… on 2026-10-28.
    assert software_dir(PINNED) == "/opt/slapgrid/d09513ebeef620b179b45cac7f97bb88"


def test_failing_promises_of_last_finished_run():
    assert failing_promises(fixture_text("instance.log")) == [
        "slappart0[default]: Promise 'check-monitor-frontend-password.py' failed with output:"
        " ERROR: Invalid URL '': No scheme supplied. Perhaps you meant https://?"
    ]


def test_failing_promises_none_when_last_run_is_clean():
    log = fixture_text("instance.log") + (
        "[2026-10-29 11:20:00,000] INFO     Processing computer partitions...\n"
        "[2026-10-29 11:20:10,000] INFO     Finished computer partitions.\n"
        "[2026-10-29 11:20:10,000] INFO     ================================================================================\n"
    )
    assert failing_promises(log) == []


def test_last_buildout_errors():
    errors = last_buildout_errors(fixture_text("software.log"))
    assert errors["/opt/slapgrid/d09513ebeef620b179b45cac7f97bb88"].startswith(
        "Error: Error downloading https://raw.githubusercontent.com/"
    )
    # The poetry failure was an internal error: no "Error:" line.
    assert "no 'Error:' line" in errors["/opt/slapgrid/4c5a1594b4b6fc0f59117f5c10cd841f"]


def test_parse_facts():
    d = software_dir(PINNED)
    output = f"{d} building\n{SEPARATOR}\n{fixture_text('software.log')}{SEPARATOR}\n{fixture_text('instance.log')}"
    facts = parse_facts([PINNED, "https://other"], output)
    assert facts.software[0].state == "building"
    assert facts.software[0].last_error.startswith("Error: Error downloading")
    assert facts.software[1].state == "absent"
    assert len(facts.failing_promises) == 1


def test_run_raises_on_failure():
    def fake_run(args, **kwargs):
        assert args == ["ssh", "host", "false"]
        return subprocess.CompletedProcess(args, 255, "", "Connection refused\n")

    with pytest.raises(NodeError, match="exit 255: Connection refused"):
        Node(("ssh", "host"), run=fake_run).run("false")


def test_facts_sends_one_command():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, f"{software_dir(PINNED)} completed\n{SEPARATOR}\n{SEPARATOR}\n", "")

    facts = Node(("ssh", "h"), run=fake_run).facts([PINNED])
    assert len(calls) == 1
    assert facts.software[0].state == "completed"
    assert facts.software[0].last_error is None


class FakeProc:
    def __init__(self, lines, returncode):
        self.stdout, self.returncode = iter(lines), returncode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def popen_returning(*runs):
    """A Popen that plays `runs` (lines, exit code) in order and records the commands."""
    runs, commands = list(runs), []

    def popen(args, **kwargs):
        commands.append(args[-1])
        lines, code = runs.pop(0)
        return FakeProc([line + "\n" for line in lines], code)

    return popen, commands


SLAPGRID_LINES = [
    "INFO Updating ncurses.",
    "INFO Installing mynij-proxy-eggs.",
    "INFO Finished software releases.",
]


def test_slapgrid_shows_changes_not_unchanged_parts():
    popen, commands = popen_returning((SLAPGRID_LINES, 0))
    out = []
    assert Node(("ssh", "h"), popen=popen).slapgrid("software --force", out=out.append) == 0
    assert commands == ["slapos node software --force"]
    assert out == ["INFO Installing mynij-proxy-eggs.", "INFO Finished software releases."]


def test_slapgrid_waits_while_busy():
    busy = (["INFO New slapos process started, but another slapos process is aleady running with pid 1, exiting."], 10)
    popen, commands = popen_returning(busy, busy, (SLAPGRID_LINES, 2))
    sleeps, out = [], []
    node = Node(("ssh", "h"), popen=popen, sleep=sleeps.append)
    assert node.slapgrid("instance --force", out=out.append) == 2  # promise failures are not an error
    assert len(commands) == 3
    assert sleeps == [15, 15]


def test_slapgrid_gives_up_when_busy_too_long(monkeypatch):
    monkeypatch.setattr("hox_cli.node.BUSY_TIMEOUT_SECONDS", 15)
    popen, _ = popen_returning(([], 10), ([], 10))
    with pytest.raises(NodeError, match="kept the lock"):
        Node(("ssh", "h"), popen=popen, sleep=lambda _: None).slapgrid("software", out=lambda _: None)


def test_slapgrid_failure():
    popen, _ = popen_returning((["INFO Error: boom"], 1))
    with pytest.raises(NodeError, match="exit 1"):
        Node(("ssh", "h"), popen=popen).slapgrid("software", out=lambda _: None)
