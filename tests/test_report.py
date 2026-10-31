from datetime import UTC, datetime

from conftest import BRANCH_URL

from hox_cli import report
from hox_cli.config import parse
from hox_cli.node import software_dir

# Lines as the node's software log has them, trimmed to what the report reads.
LOG = f"""\
[2026-10-30 20:04:18,381] INFO     Processing software releases...
[2026-10-30 20:04:18,908] INFO     Installing software release {BRANCH_URL} ...
[2026-10-30 20:04:57,291] INFO     Installing ncurses.
[2026-10-30 20:06:12,223] INFO     Installing zlib.
[2026-10-30 20:55:45,965] DEBUG    https://slap.vifib.com:443 "POST /availableSoftwareRelease HTTP/1.1" 200 4
[2026-10-30 20:55:45,966] INFO     Finished software releases.
[2026-10-30 21:11:12,000] INFO     Processing software releases...
[2026-10-30 21:11:12,315] INFO     Installing software release https://example.org/a/software.cfg ...
[2026-10-30 21:12:00,000] INFO     Installing gcc.
[2026-10-30 21:20:00,000] ERROR    Failed to run buildout profile in directory '/opt/slapgrid/x'
[2026-10-30 21:20:01,000] INFO     Finished software releases.
[2026-10-30 22:00:00,000] INFO     Processing software releases...
[2026-10-30 22:00:01,000] INFO     Installing software release https://example.org/a/software.cfg ...
[2026-10-30 22:01:00,000] INFO     Installing llvm.
[2026-10-30 22:30:00,000] INFO     Processing software releases...
[2026-10-30 22:30:01,000] INFO     Installing software release https://example.org/a/software.cfg ...
[2026-10-30 22:31:00,000] INFO     Installing llvm.
"""
NOW = datetime(2026, 10, 30, 23, 31, tzinfo=UTC)


def test_parse_runs():
    done, failed, killed, running = report.parse_runs(LOG)
    assert (done.url, done.result, done.end) == (BRANCH_URL, "completed", datetime(2026, 10, 30, 20, 55, 45, tzinfo=UTC))
    assert done.part_times(NOW) == [("ncurses", 75.0), ("zlib", 2973.0)]
    assert failed.result == "failed"
    # Killed (no end in the log): it ends at its last line, before the next slapgrid run.
    assert (killed.result, killed.end) == ("stopped", datetime(2026, 10, 30, 22, 1, tzinfo=UTC))
    assert (running.result, running.end) == ("building", None)
    assert running.part_times(NOW) == [("llvm", 3600.0)]


def test_markdown(config_data, tmp_path):
    config = parse(config_data, tmp_path)
    sizes = {software_dir(BRANCH_URL): 138 * 1024, f"{report.SHARED}/gcc-14.3": 1800 * 1024}
    usage = report.NodeUsage(NOW, 4, 8000, "-j4", (9 * 1024**2, 99 * 1024**2), sizes)
    text = report.markdown(config, config.software_named(None), report.parse_runs(LOG), usage)
    assert "## mynij-proxy branch" in text
    assert "| 1 | 2026-10-30 20:04 | 2026-10-30 20:55 | 51 min | completed |" in text
    assert "example.org" not in text  # another software's runs
    assert "| ncurses | 1 min |" in text
    assert "| mynij-proxy branch: release directory | 138 MiB |" in text
    assert "| &nbsp;&nbsp;gcc-14.3 | 1.8 GiB |" in text
    assert "| Node disk used (all of it) | 9.0 GiB of 99.0 GiB |" in text
