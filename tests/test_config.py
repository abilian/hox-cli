import tomllib
from pathlib import Path

import pytest

from conftest import BRANCH_URL
from hox_cli.config import ConfigError, find, load, parse

ROOT = Path("/work/hox")


def test_parse(config_data):
    config = parse(config_data, ROOT)
    sw = config.software_named(None)
    assert sw.branch_url == BRANCH_URL
    assert sw.pinned_url("971d944") == BRANCH_URL.replace("/mynij-proxy/software/", "/971d944/software/")
    assert sw.checkout == ROOT / "slapos_abilian_edge"
    assert sw.checks[0].expect_status == 200
    assert config.master.cert == Path("~/.slapos/client.crt").expanduser()
    # ssh -F resolves against hox.toml's directory; plain words stay as they are.
    assert config.node.ssh == ("ssh", "-F", str(ROOT / "node-deployer/ssh_config"), "hox-node")


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["software"]["mynij-proxy"].update(typo=1), r"\[software.mynij-proxy\] unknown keys: typo"),
        (lambda d: d["node"].pop("ssh"), r"\[node\] missing keys: ssh"),
        (lambda d: d.pop("project"), r"\[project\] is required"),
        (lambda d: d.update(extra={}), "unknown sections: extra"),
        (lambda d: d["software"].clear(), "at least one is required"),
        (lambda d: d["software"]["mynij-proxy"]["checks"][0].pop("url"), r"checks\[0\]\] missing keys: url"),
    ],
)
def test_parse_errors(config_data, change, message):
    change(config_data)
    with pytest.raises(ConfigError, match=message):
        parse(config_data, ROOT)


def test_software_named(config_data):
    config_data["software"]["sbe"] = dict(config_data["software"]["mynij-proxy"], instance="sbe-1")
    config = parse(config_data, ROOT)
    assert config.software_named("sbe").instance == "sbe-1"
    with pytest.raises(ConfigError, match="several software"):
        config.software_named(None)
    with pytest.raises(ConfigError, match="no software 'x'"):
        config.software_named("x")


def test_find_and_load(tmp_path):
    (tmp_path / "hox.toml").write_text('[master]\nurl = "u"\ncert = "c"\nkey = "k"\n')
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert find(tmp_path / "a" / "b") == tmp_path / "hox.toml"
    with pytest.raises(ConfigError, match=r"\[project\] is required"):
        load(tmp_path / "hox.toml")


def test_find_none(tmp_path):
    with pytest.raises(ConfigError, match="no hox.toml"):
        find(tmp_path)


def test_example_config_is_valid():
    example = Path(__file__).parent.parent / "hox.example.toml"
    config = parse(tomllib.loads(example.read_text()), ROOT)
    sw = config.software_named(None)
    assert sw.checkout == Path("/work/hox/slapos_abilian_edge")
    assert [c.name for c in sw.checks] == ["backend ping", "frontend ping", "CORS"]
