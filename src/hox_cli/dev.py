"""The dev loop: pushed? served by GitHub? rebuild in place, then check."""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from pathlib import PurePosixPath


from .node import Node, git
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Check, Software
    import httpx2

GITHUB_POLL_SECONDS = 15
GITHUB_TIMEOUT_SECONDS = (
    6 * 60
)  # raw.githubusercontent.com caches branch files up to 5 minutes
CURL_TIMEOUT_SECONDS = 20


class DevError(Exception):
    pass


def wait_for(what: str, find, sleep=time.sleep, poll: int = 5, timeout: int = 180):
    """The first of find()'s results, once there is one."""
    waited = 0
    while not (rows := find()):
        if waited >= timeout:
            msg = f"{what}: still not there after {waited // 60} min {waited % 60} s"
            raise DevError(msg)
        sleep(poll)
        waited += poll
    return rows[0]


def require_pushed(sw: Software) -> str:
    """The local branch head, if origin has it too (asks the remote: no fetch, no write)."""
    head = git(sw.checkout, "rev-parse", sw.branch).strip()
    line = git(sw.checkout, "ls-remote", "origin", f"refs/heads/{sw.branch}").strip()
    remote = line.split()[0] if line else None
    if remote != head:
        msg = f"{sw.branch} is at {head[:9]} locally and {remote[:9] if remote else 'absent'} on origin: push it first"
        raise DevError(msg)
    return head


def pushed_commit(sw: Software, commit: str) -> str:
    """The full SHA of commit, if a branch of origin contains it (as last fetched).

    Pinned URLs use it: GitHub also serves short SHAs, but each spelling is
    another URL for the master and another software directory on the node.
    """
    sha = git(sw.checkout, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    if not git(sw.checkout, "branch", "-r", "--contains", sha).strip():
        msg = f"{commit} is on no branch of origin (as last fetched): push it first"
        raise DevError(msg)
    return sha


def software_files(sw: Software, commit: str) -> dict[str, str]:
    """The files of the software's directory at commit, as the node will fetch them."""
    directory = str(PurePosixPath(sw.path).parent)
    names = git(sw.checkout, "ls-tree", "-r", "--name-only", commit, directory).split()
    return {name: git(sw.checkout, "show", f"{commit}:{name}") for name in names}


def stale_files(sw: Software, files: dict[str, str], http: httpx2.Client) -> list[str]:
    stale = []
    for name, content in files.items():
        response = http.get(sw.raw_url(name))
        if response.status_code != 200 or response.text != content:
            stale.append(name)
    return stale


def wait_for_github(
    sw: Software, commit: str, http: httpx2.Client, out=print, sleep=time.sleep
) -> None:
    files = software_files(sw, commit)
    waited = 0
    while stale := stale_files(sw, files, http):
        if waited >= GITHUB_TIMEOUT_SECONDS:
            msg = f"raw.githubusercontent.com still serves old versions of: {', '.join(stale)}"
            raise DevError(msg)
        out(
            f"raw.githubusercontent.com serves old {', '.join(stale)}, retrying in {GITHUB_POLL_SECONDS}s"
        )
        sleep(GITHUB_POLL_SECONDS)
        waited += GITHUB_POLL_SECONDS
    out(f"raw.githubusercontent.com serves {commit[:9]} ({len(files)} files)")


@dataclass(frozen=True)
class CheckResult:
    name: str
    url: str | None  # None: a connection parameter it needs is not published
    ok: bool
    detail: str


def run_checks(
    checks: tuple[Check, ...], connection: dict[str, str], node: Node
) -> list[CheckResult]:
    """Run each check with curl on the node: backends are only reachable over re6st."""
    published = {k: v for k, v in connection.items() if v not in {None, "None", ""}}
    results = []
    for check in checks:
        try:
            url = check.url.format_map(published)
        except KeyError as e:
            results.append(
                CheckResult(
                    check.name, None, False, f"{e.args[0]} is not published yet"
                )
            )
            continue
        results.append(evaluate(check, url, node.run(curl_command(check, url))))
    return results


def curl_command(check: Check, url: str) -> str:
    args = [
        "curl",
        "-gsk",
        "-o",
        "/dev/null",
        "-D",
        "-",
        "--max-time",
        str(CURL_TIMEOUT_SECONDS),
    ]
    for key, value in check.headers.items():
        args += ["-H", f"{key}: {value}"]
    # Always exit 0 so the output is ours to read; curl's own code comes last.
    return shlex.join([*args, url]) + '; echo "curl-exit=$?"'


def evaluate(check: Check, url: str, output: str) -> CheckResult:
    lines = output.strip().splitlines()
    curl_exit = int(lines[-1].removeprefix("curl-exit="))
    if curl_exit != 0:
        return CheckResult(check.name, url, False, f"curl exit {curl_exit}")
    status_line = next(line for line in lines if line.startswith("HTTP/"))
    status = int(status_line.split()[1])
    headers = {}
    for line in lines:
        if ":" in line and not line.startswith("HTTP/"):
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    problems = []
    if check.expect_status is not None and status != check.expect_status:
        problems.append(f"status {status}, expected {check.expect_status}")
    for key, value in check.expect_header.items():
        if headers.get(key.lower()) != value:
            problems.append(f"{key}: {headers.get(key.lower())!r}, expected {value!r}")
    return CheckResult(
        check.name, url, not problems, "; ".join(problems) or f"status {status}"
    )
