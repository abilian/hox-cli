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
    parse_system,
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

    with pytest.raises(NodeError, match=r"^ssh host: Connection refused$"):
        Node(("ssh", "host"), run=fake_run).run("false")


def test_run_names_the_command_that_failed_on_the_node():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "nope\n")

    with pytest.raises(NodeError, match="'false': exit 1: nope"):
        Node(("ssh", "host"), run=fake_run).run("false")


def test_parse_system():
    parts = [
        "392400.12\n",
        "/srv/slapgrid/slappart10\n/srv/slapgrid/slappart2\n/srv/slapgrid/slappart0\n",
        "slappart0:web-2aa681dd2c1eb6c7422d9a9becca20d9-on-watch   RUNNING   pid 1, uptime 1:00:00\n"
        "slappart0:release-31442f67c794e402600c64f31ae3672b        EXITED    Nov 05 06:05 PM\n"
        "slappart2:web-2aa681dd2c1eb6c7422d9a9becca20d9-on-watch   BACKOFF   Exited too quickly\n"
        "slappart2:postgres-start-on-watch                         RUNNING   pid 2, uptime 1:00:00\n"
        "slappart2:bootstrap-monitor                               FATAL     Exited too quickly\n"
        "slappart2:crond-on-watch                                  EXITED    Nov 05 06:05 PM\n"
        "slappart2:release-31442f67c794e402600c64f31ae3672b        STOPPED   Not started\n"
        "slappart2:redis-on-watch                                  STOPPED   Not started\n",
        "slapuser0  102400\nslapuser0  1024\nroot  999999\nslapuser2  2048\n",
        "               total        used        free      shared  buff/cache   available\n"
        "Mem:            7900        4100         300          12        3500        3700\n"
        "Swap:              0           0           0\n",
        "Filesystem     1M-blocks  Used Available Use% Mounted on\n/dev/vda1 120000 76000 44000 64% /\n",
    ]
    system = parse_system(f"{SEPARATOR}\n".join(parts))
    assert system.uptime_seconds == 392400
    assert [p.name for p in system.partitions] == ["slappart0", "slappart2", "slappart10"]
    web, broken, empty = system.partitions
    assert (web.memory_mb, web.running, web.down) == (101, 1, [])
    assert (broken.memory_mb, broken.running, broken.down) == (2, 1, ["web", "bootstrap-monitor", "crond", "redis"])
    assert (empty.memory_mb, empty.running, empty.down) == (0, 0, [])
    assert (system.memory_available_mb, system.memory_total_mb) == (3700, 7900)
    assert (system.disk_free_mb, system.disk_total_mb) == (44000, 120000)


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


def test_ssh_failure_shows_what_ssh_said():
    popen, _ = popen_returning((["INFO Updating ncurses.", "Connection to 10.73.0.3 closed by remote host."], 255))
    with pytest.raises(NodeError, match="ssh failed; its last lines:\nINFO Updating ncurses.\nConnection to 10.73.0.3 closed"):
        Node(("ssh", "h"), popen=popen).slapgrid("software", out=lambda _: None)
