"""The git and GitHub side of the dev loop, on real git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx2
import pytest

from hox_cli import dev
from hox_cli.config import Software
from hox_cli.dev import DevError


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    """A checkout with branch mynij-proxy pushed to a bare origin."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "mynij-proxy", str(work)], check=True)
    sw_dir = work / "software" / "mynij-proxy"
    sw_dir.mkdir(parents=True)
    (sw_dir / "software.cfg").write_text("[buildout]\n")
    (sw_dir / "instance.cfg.in").write_text("[instance]\n")
    (work / "README").write_text("not part of the software\n")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "mynij-proxy")
    return work


def software(checkout: Path, checks=()) -> Software:
    return Software(
        name="mynij-proxy",
        repo="abilian/slapos_abilian_edge",
        checkout=checkout,
        branch="mynij-proxy",
        path="software/mynij-proxy/software.cfg",
        software_type="default",
        product="mynij-proxy",
        instance="mynij-proxy-3",
        checks=checks,
    )


def test_require_pushed(repo):
    assert dev.require_pushed(software(repo)) == git(repo, "rev-parse", "HEAD").strip()


def test_require_pushed_refuses_local_commits(repo):
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "local",
    )
    with pytest.raises(DevError, match="push it first"):
        dev.require_pushed(software(repo))


def test_software_files_are_the_software_directory(repo):
    files = dev.software_files(software(repo), "HEAD")
    assert files == {
        "software/mynij-proxy/instance.cfg.in": "[instance]\n",
        "software/mynij-proxy/software.cfg": "[buildout]\n",
    }


def serving(files: dict[str, str]) -> httpx2.Client:
    """raw.githubusercontent.com serving `files` (path within the repo -> content)."""
    prefix = "/abilian/slapos_abilian_edge/mynij-proxy/"

    def handler(request):
        name = request.url.path.removeprefix(prefix)
        return (
            httpx2.Response(200, text=files[name])
            if name in files
            else httpx2.Response(404)
        )

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_wait_for_github_retries_until_fresh(repo):
    sw = software(repo)
    served = {
        "software/mynij-proxy/software.cfg": "[buildout]\n",
        "software/mynij-proxy/instance.cfg.in": "old\n",
    }
    sleeps, out = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        served["software/mynij-proxy/instance.cfg.in"] = (
            "[instance]\n"  # the cache expires
        )

    dev.wait_for_github(sw, "HEAD", serving(served), out=out.append, sleep=sleep)
    assert sleeps == [dev.GITHUB_POLL_SECONDS]
    assert "serves old software/mynij-proxy/instance.cfg.in" in out[0]


def test_wait_for_github_gives_up(repo, monkeypatch):
    monkeypatch.setattr(dev, "GITHUB_TIMEOUT_SECONDS", 30)
    with pytest.raises(
        DevError, match="still serves old versions of: .*instance.cfg.in"
    ):
        dev.wait_for_github(
            software(repo),
            "HEAD",
            serving({}),
            out=lambda _: None,
            sleep=lambda _: None,
        )


def test_pushed_commit_is_the_full_sha(repo):
    git(repo, "fetch", "-q", "origin")
    head = git(repo, "rev-parse", "HEAD").strip()
    assert dev.pushed_commit(software(repo), head[:7]) == head


def test_pushed_commit_refuses_local_commits(repo):
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "local",
    )
    with pytest.raises(DevError, match="on no branch of origin"):
        dev.pushed_commit(software(repo), "HEAD")
