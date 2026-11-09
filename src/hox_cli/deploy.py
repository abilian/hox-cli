"""`hox deploy`: wait for a requested instance to be allocated, built, and to pass its checks.

The node's own cron does the work (it builds what is supplied and deploys what
is allocated): this only waits, says what it waits for, and forces one
instance run once the build is there.
"""

import time

from . import dev, status
from .config import Software
from .master import Api
from .node import SLAPGRID_PROMISE_FAIL, Node

ALLOCATION_POLL_SECONDS, ALLOCATION_TIMEOUT_SECONDS = 30, 30 * 60
BUILD_POLL_SECONDS, BUILD_TIMEOUT_SECONDS = 60, 8 * 3600  # SBE without cache: hours
CHECKS_POLL_SECONDS, CHECKS_TIMEOUT_SECONDS = 60, 45 * 60  # frontends take a while


def wait_allocated(api: Api, sw: Software, out=print, sleep=time.sleep) -> str:
    def find() -> list[str]:
        inst = status.instance(api, sw.instance)
        return [inst.partition] if inst and inst.partition else []

    out(f"{sw.instance}: waiting for allocation")
    partition = dev.wait_for(
        f"{sw.instance}'s allocation",
        find,
        sleep,
        ALLOCATION_POLL_SECONDS,
        ALLOCATION_TIMEOUT_SECONDS,
    )
    out(f"{sw.instance}: allocated on {partition}")
    return partition


def wait_built(node: Node, url: str, out=print, sleep=time.sleep) -> None:
    last = None

    def find() -> list[bool]:
        nonlocal last
        [state] = node.facts([url]).software
        if (state.state, state.last_error) != last:
            out(
                f"build of {url}: {state.state}"
                + (f" (last error: {state.last_error})" if state.last_error else "")
            )
            last = (state.state, state.last_error)
        return [True] if state.state == "completed" else []

    dev.wait_for(
        f"build of {url}", find, sleep, BUILD_POLL_SECONDS, BUILD_TIMEOUT_SECONDS
    )


def wait_checks(
    api: Api, sw: Software, node: Node, out=print, sleep=time.sleep
) -> list[dev.CheckResult]:
    last = None

    def find() -> list[list[dev.CheckResult]]:
        nonlocal last
        results = dev.run_checks(
            sw.checks, status.require_instance(api, sw).connection, node
        )
        failing = [f"{r.name}: {r.detail}" for r in results if not r.ok]
        if failing and failing != last:
            out("waiting for: " + "; ".join(failing))
        last = failing
        return [] if failing else [results]

    return dev.wait_for(
        f"{sw.instance}'s checks",
        find,
        sleep,
        CHECKS_POLL_SECONDS,
        CHECKS_TIMEOUT_SECONDS,
    )


def deploy(
    api: Api, sw: Software, url: str, node: Node, out=print, sleep=time.sleep
) -> list[dev.CheckResult]:
    """After `hox setup` and `hox request`: allocated, built, deployed, checks passing."""
    partition = wait_allocated(api, sw, out, sleep)
    wait_built(node, url, out, sleep)
    # Only this instance's partition: another one's failure (a release still building) isn't ours.
    if (
        node.slapgrid(f"instance --force --only-cp {partition}", out)
        == SLAPGRID_PROMISE_FAIL
    ):
        out(
            "some promises fail (see above); they may pass once the frontends are there"
        )
    return wait_checks(api, sw, node, out, sleep)
