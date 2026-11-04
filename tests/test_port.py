import pytest

from hox_cli.port import PortError, port, software_cfg, versions_cfg, write

HOP3_TOML = """
[metadata]
id = "cal"
version = "1.0"
title = "Cal"

[build]
toolchain = "python"
before-build = "bash scripts/download.sh"
build = "pip install -r requirements.txt"

[run]
before-run = ["bash scripts/setup.sh", "echo ready"]
start = "python -m cal --port $PORT"

[run.workers]
mailer = "cal-mailer"

[env]
MODE = "it's on"
SECRET_KEY = { generate = "hex" }
SITE = { key = "domain" }

[env.computed]
DB = "${PGHOST}:${PGPORT}"

[[volumes]]
name = "data"
target = "var/data"

[[volumes]]
name = "cache"
target = "cache"
type = "scratch"

[admin]
username = "admin"
email = "operator"
password = { generate = "password" }

[probe]
create = "true"

[[addons]]
type = "postgres"

[[addons]]
type = "redis"

[healthcheck]
path = "/health"

[waf]
mode = "block"
"""

def pypi(name, version):
    return {
        "cal": ["cal-1.0-py3-none-any.whl"],
        "bcrypt": ["bcrypt-5.0.0.tar.gz", "bcrypt-5.0.0-cp39-abi3-manylinux_2_34_x86_64.whl"],
        "oldlib": ["oldlib-0.1.tar.gz"],
        "misaka": ["misaka-2.1.1.tar.gz"],
    }[name]


@pytest.fixture
def recipe(tmp_path):
    d = tmp_path / "cal"
    (d / "scripts").mkdir(parents=True)
    (d / "screenshots").mkdir()
    (d / "hop3.toml").write_text(HOP3_TOML)
    (d / "requirements.txt").write_text(
        "# pinned\ncal==1.0\nbcrypt==5.0.0  # via cal\noldlib==0.1\nwinlib==1 ; sys_platform == 'win32'\n")
    (d / "scripts" / "download.sh").write_text("mkdir -p var/data\n")
    for overlay in ("catalog.toml", "readme.md", "readme-fr.md", "icon.webp", "check.py", "screenshots/a.png"):
        (d / overlay).write_text("")
    return d


def test_port(recipe):
    release = port(recipe, "3.13", pypi)
    assert release.name == "cal"
    assert list(release.files) == ["hop3.toml", "requirements.txt", "scripts/download.sh"]
    assert release.processes == {"release": ["bash scripts/setup.sh", "echo ready"],
                                 "web": ["python -m cal --port $PORT"], "mailer": ["cal-mailer"]}
    assert release.volumes == ["data var/data"] and release.database
    cfg = software_cfg(release)
    assert "  ../../component/postgresql/buildout.cfg\n  ../../stack/hop3/buildout.cfg\n" in cfg
    assert "<= python3.13" in cfg
    assert "  scripts/download.sh " in cfg
    assert "commands =\n  bash scripts/download.sh\n" in cfg
    assert "eggs =\n  cal\n  bcrypt\n  oldlib\n" in cfg
    assert "health = /health" in cfg
    assert "secrets = SECRET_KEY HOP3_ADMIN_PASSWORD" in cfg
    assert "  MODE='it'\"'\"'s on'\n" in cfg
    assert "  SITE=$DOMAIN\n" in cfg
    assert '  DB="$PGHOST:$PGPORT"\n' in cfg
    assert "  HOP3_ADMIN_USER=admin\n" in cfg
    assert "volumes =\n  data var/data\n\n" in cfg
    assert "release = bash scripts/setup.sh && echo ready\n" in cfg
    assert "web = python -m cal --port $PORT\n" in cfg
    assert "mailer = cal-mailer\n" in cfg
    assert "${" not in cfg.replace("${:_profile_base_location_}", "").replace("${postgresql:location}", "")
    assert "nodejs" not in cfg and "egg =" not in cfg
    assert release.notes == [
        "[env] SECRET_KEY: generated as a 32-character password, not 'hex'",
        "[admin] email: set HOP3_ADMIN_EMAIL as an instance parameter",
        "[probe]: not created, nothing on SlapOS signs in with it",
        "[[addons]] redis: not supported",
        "[waf]: not mapped",
    ]
    versions = versions_cfg(release)
    assert "bcrypt = 5.0.0:whl" in versions and "cal    = 1.0:whl" in versions
    assert "oldlib = 0.1\n" in versions and "winlib" not in versions


def test_write(recipe, tmp_path):
    out = tmp_path / "software" / "cal"
    (out / "hop3" / "stale").mkdir(parents=True)
    write(port(recipe, "3.13", pypi), out)
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == [
        "hop3/hop3.toml", "hop3/requirements.txt", "hop3/scripts/download.sh", "software.cfg", "versions.cfg"]


@pytest.mark.parametrize("old, new, error", [
    ('toolchain = "python"', 'toolchain = "node"', "toolchain 'node'"),
    ('build = "pip install', 'build = "make" #', "only 'pip install -r requirements.txt'"),
    ('start = "python -m cal --port $PORT"', 'start = "cal --port ${PORT}x"', "commands must be one line"),
    ('before-build = "bash', 'before-build = "X=${HOME} bash', "commands must be one line"),
])
def test_port_refuses(recipe, old, new, error):
    toml = recipe / "hop3.toml"
    toml.write_text(toml.read_text().replace(old, new))
    with pytest.raises(PortError, match=error):
        port(recipe, "3.13", pypi)


def test_port_needs_pins(recipe):
    (recipe / "requirements.txt").write_text("cal>=1.0\n")
    with pytest.raises(PortError, match="isn't pinned"):
        port(recipe, "3.13", pypi)


def test_port_needs_requirements_or_source(recipe):
    (recipe / "requirements.txt").unlink()
    with pytest.raises(PortError, match="no requirements.txt.*--source"):
        port(recipe, "3.13", pypi)


def test_port_from_source(recipe, tmp_path):
    (recipe / "requirements.txt").unlink()
    toml = recipe / "hop3.toml"
    toml.write_text(toml.read_text().replace('before-build = "bash scripts/download.sh"',
                                             'before-build = ["bash scripts/download.sh", "npm ci"]'))
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname = "cal-app"\n')
    resolved = []

    def resolve(pyproject, python):
        resolved.append((pyproject, python))
        return "bcrypt==5.0.0\nsetuptools==84.0\noldlib==0.1\n"

    release = port(recipe, "3.13", pypi, source=source, resolve=resolve)
    assert resolved == [(source / "pyproject.toml", "3.13")]
    assert release.egg == "cal-app" and release.node
    assert release.eggs == ["bcrypt", "oldlib"]  # setuptools is SlapOS's
    cfg = software_cfg(release)
    assert "egg = cal-app\neggs =\n  ${python-app-develop:egg}\n  bcrypt\n" in cfg
    assert "  ../../component/nodejs/buildout.cfg\n" in cfg
    assert "[nodejs]\n<= nodejs-24.11.0\n" in cfg
    assert "tools = ${nodejs:location}/bin:${:base-tools}\n" in cfg
    assert "From the app's pyproject.toml by `hox port`" in versions_cfg(release)
    assert any("resolved" in n for n in release.notes)


def test_port_uses_the_component_of_a_package_that_needs_one(recipe):
    requirements = recipe / "requirements.txt"
    requirements.write_text(requirements.read_text() + "misaka==2.1.1\n")
    cfg = software_cfg(port(recipe, "3.13", pypi))
    assert "  ../../component/python-misaka/buildout.cfg\n" in cfg
    assert "  ${python-misaka:egg}\n" in cfg
    assert "  misaka\n" not in cfg


def test_port_source_needs_a_project(recipe, tmp_path):
    (recipe / "requirements.txt").unlink()
    with pytest.raises(PortError, match="no pyproject.toml"):
        port(recipe, "3.13", pypi, source=tmp_path)
