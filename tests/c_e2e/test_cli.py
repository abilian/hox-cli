"""The installed `hox` command, run as a process: its entry point, argument
parsing, output and exit codes. Offline: nothing here reaches a master or a node."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOX = Path(sys.executable).parent / "hox"  # the console script of the test environment

LOCK = """
[[package]]
name = "app"
version = "0.1"
source = { editable = "." }
dependencies = [{ name = "flask" }]

[[package]]
name = "flask"
version = "3.1.2"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/flask-3.1.2.tar.gz" }
wheels = [{ url = "https://x/flask-3.1.2-py3-none-any.whl" }]
"""


def hox(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HOX), *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )


def test_help_lists_the_commands(tmp_path):
    result = hox("--help", cwd=tmp_path)
    assert result.returncode == 0
    for command in (
        "deploy",
        "rebuild",
        "status",
        "instances",
        "port",
        "versions",
        "try-start",
    ):
        assert command in result.stdout


def test_versions_prints_buildout_pins_without_a_config(tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK)
    result = hox("versions", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("[versions]\nflask = 3.1.2:whl\n")


def test_a_missing_config_fails_with_one_line_and_exit_1(tmp_path):
    result = hox("--config", str(tmp_path / "nowhere.toml"), "status", cwd=tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("hox: ")
    assert result.stderr.count("\n") == 1
