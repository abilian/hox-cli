"""The compute node, over SSH, and the local git checkouts."""

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOFTWARE_ROOT = "/opt/slapgrid"
SOFTWARE_LOG = "/opt/slapos/log/slapos-node-software.log"
INSTANCE_LOG = "/opt/slapos/log/slapos-node-instance.log"
LOG_LINES = 2000
SEPARATOR = "----hox----"


class NodeError(Exception):
    pass


def software_dir(url: str) -> str:
    return f"{SOFTWARE_ROOT}/{hashlib.md5(url.encode()).hexdigest()}"


@dataclass(frozen=True)
class SoftwareState:
    url: str
    state: str  # "completed", "building" (dir present, not completed) or "absent"
    last_error: str | None  # last buildout error for this dir, if the last run of it failed


@dataclass(frozen=True)
class NodeFacts:
    software: list[SoftwareState]
    failing_promises: list[str]  # from the last `slapos node instance` run


class Node:
    def __init__(self, ssh: tuple[str, ...], run=subprocess.run):
        self._ssh = ssh
        self._run = run

    def run(self, command: str) -> str:
        result = self._run([*self._ssh, command], capture_output=True, text=True)
        if result.returncode != 0:
            raise NodeError(f"{shlex.join(self._ssh)} {command!r}: exit {result.returncode}: {result.stderr.strip()[-500:]}")
        return result.stdout

    def facts(self, urls: list[str]) -> NodeFacts:
        dirs = [software_dir(u) for u in urls]
        script = "; ".join(
            [f"for d in {' '.join(dirs)}; do"
             " if [ -f $d/.completed ]; then echo $d completed;"
             " elif [ -d $d ]; then echo $d building; else echo $d absent; fi; done" if dirs else "true",
             f"echo {SEPARATOR}", f"tail -n {LOG_LINES} {SOFTWARE_LOG}",
             f"echo {SEPARATOR}", f"tail -n {LOG_LINES} {INSTANCE_LOG}"]
        )
        return parse_facts(urls, self.run(script))


def parse_facts(urls: list[str], output: str) -> NodeFacts:
    states, software_log, instance_log = output.split(SEPARATOR + "\n")
    by_dir = dict(line.split(" ", 1) for line in states.splitlines())
    errors = last_buildout_errors(software_log)
    software = []
    for url in urls:
        d = software_dir(url)
        state = by_dir.get(d, "absent")
        software.append(SoftwareState(url, state, errors.get(d) if state != "completed" else None))
    return NodeFacts(software, failing_promises(instance_log))


_FAILED = re.compile(r"Failed to run buildout profile in directory '([^']+)'")
_ERROR = re.compile(r"INFO\s+(Error: .*)")
_RUN_START = re.compile(r"Processing software releases\.\.\.")


def last_buildout_errors(log: str) -> dict[str, str]:
    """Software dir -> the 'Error:' line of its last failed buildout run.

    A later successful run in the same log clears nothing here: callers only
    look at dirs that are not completed.
    """
    errors: dict[str, str] = {}
    last_error = None
    for line in log.splitlines():
        if _RUN_START.search(line):
            last_error = None
        elif m := _ERROR.search(line):
            last_error = m.group(1)
        elif m := _FAILED.search(line):
            errors[m.group(1)] = last_error or "buildout failed (no 'Error:' line in the log tail)"
    return errors


def failing_promises(log: str) -> list[str]:
    """Promise failures of the last finished `slapos node instance` run.

    A run logs "Finished computer partitions.", then, if promises failed,
    "Error with promises for the following partitions:" and one line each.
    """
    lines = log.splitlines()
    finished = [i for i, line in enumerate(lines) if "Finished computer partitions." in line]
    if not finished:
        return []
    result = []
    in_block = False
    for line in lines[finished[-1] + 1:]:
        if re.search(r"INFO\s+=+$", line):
            continue
        if "Error with promises for the following partitions:" in line:
            in_block = True
        elif in_block and (m := re.search(r"INFO\s{3,}(slappart\S+: .*)", line)):
            result.append(m.group(1))
        else:
            break
    return result


def git(checkout: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise NodeError(f"git -C {checkout} {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout
