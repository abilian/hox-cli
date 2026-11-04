import hashlib

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
        "setuptools": ["setuptools-84.0.tar.gz", "setuptools-84.0-py3-none-any.whl"],
        "cffi": ["cffi-2.1.1.tar.gz", "cffi-2.1.1-cp313-cp313-manylinux_2_28_x86_64.whl"],
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
    assert "secrets = HOP3_ADMIN_PASSWORD SECRET_KEY:hex" in cfg
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
        "[admin] email: admin@cal.invalid, unless HOP3_ADMIN_EMAIL is an instance parameter",
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


def test_port_venv_builds_compiled_packages_from_source(recipe, tmp_path):
    hashed = []

    def hashes(pins, python):
        hashed.append(([p.name for p in pins], python))
        return "bcrypt==5.0.0 \\\n    --hash=sha256:abc\n"

    release = port(recipe, "3.13", pypi, venv=True, hashes=hashes)
    assert hashed == [(["bcrypt", "cal", "oldlib"], "3.13")]
    cfg = software_cfg(release)
    assert "  ../../stack/hop3/buildout.cfg\n  ../../stack/python-app/venv.cfg\n  ../../stack/hop3/venv.cfg\n" in cfg
    assert "no-binary = bcrypt\n" in cfg  # oldlib has only an sdist, cal a pure wheel
    assert "uploaded-prior-to = 20" in cfg
    assert "versions.cfg" not in cfg and "eggs =" not in cfg
    md5 = hashlib.md5(release.requirements.encode()).hexdigest()
    assert f"[python-app-requirements]\nurl = ${{:_profile_base_location_}}/requirements.txt\nmd5sum = {md5}\n" in cfg
    write(release, tmp_path)
    assert (tmp_path / "requirements.txt").read_text() == release.requirements
    assert not (tmp_path / "versions.cfg").exists()


def test_port_venv_keeps_what_buildout_would_provide(recipe):
    requirements = recipe / "requirements.txt"
    requirements.write_text(requirements.read_text() + "setuptools==84.0\n")
    release = port(recipe, "3.13", pypi, venv=True, hashes=lambda pins, python: "")
    assert "setuptools" in release.eggs


def test_port_venv_uses_the_library_a_package_needs(recipe):
    requirements = recipe / "requirements.txt"
    requirements.write_text(requirements.read_text() + "cffi==2.1.1\n")
    cfg = software_cfg(port(recipe, "3.13", pypi, venv=True, hashes=lambda pins, python: ""))
    assert "  ../../component/libffi/buildout.cfg\n" in cfg
    assert "build-libraries = ${libffi:location}\n" in cfg
    assert "no-binary = bcrypt cffi\n" in cfg


def test_port_venv_is_for_a_python_app(go_app, go_source):
    with pytest.raises(PortError, match="--venv"):
        port(go_app, "3.13", source=go_source, venv=True)


def test_port_source_needs_a_project(recipe, tmp_path):
    (recipe / "requirements.txt").unlink()
    with pytest.raises(PortError, match="no pyproject.toml"):
        port(recipe, "3.13", pypi, source=tmp_path)


GO_APP = """
[metadata]
id = "forge"
version = "1.0"

[build]
before-build = "bash scripts/download.sh"
build = "make forge"

[run]
start = "./forge web"

[env]
TOKEN = { generate = "hex" }
JWT = { generate = "urlsafe" }
KEY = { generate = "base64" }
PLAIN = { generate = "password", length = 20 }
ID = { generate = "uuid" }
SHORT = { generate = "hex", length = 8 }

[admin]
username = "root"
email = "operator"
password = { generate = "password" }
create = '''
set -e
USER_NAME="${HOP3_ADMIN_USER:?not injected}"
./forge admin create "$USER_NAME"
'''
"""


@pytest.fixture
def go_app(tmp_path):
    recipe = tmp_path / "forge"
    (recipe / "scripts").mkdir(parents=True)
    (recipe / "hop3.toml").write_text(GO_APP)
    (recipe / "scripts/download.sh").write_text("hop3-fetch https://example.org/forge.tar.gz | tar xz\n")
    return recipe


@pytest.fixture
def go_source(tmp_path):
    source = tmp_path / "forge-src"
    source.mkdir()
    (source / "go.mod").write_text("module example.org/forge\n")
    return source


def _no_pypi(name, version):
    raise AssertionError(f"{name} {version}: a Go app has no eggs")


@pytest.mark.parametrize("go_mod, component", [
    ("module x\n", "1.23"),  # says nothing
    ("module x\n\ngo 1.9\n", "1.23"),  # as a number, 9 is below 23
    ("module x\n\ngo 1.23.4\n", "1.23"),
    ("module x\n\ngo 1.24.0\n", "1.24"),
    ("module x\n\ngo 1.25.0\ntoolchain go1.25.8\n", "1.26"),  # SlapOS has no 1.25
])
def test_port_go_component_from_go_mod(go_app, go_source, go_mod, component):
    (go_source / "go.mod").write_text(go_mod)
    assert f"tools = ${{golang{component}:location}}/bin:" in software_cfg(port(go_app, "3.13", _no_pypi, source=go_source))


def test_port_go_app_with_a_node_frontend_has_both_on_its_path(go_app, go_source):
    toml = go_app / "hop3.toml"
    toml.write_text(toml.read_text().replace('build = "make forge"', 'build = ["NODE_OPTIONS=--max-old-space-size=3072 npx pnpm --dir web build", "make forge"]'))
    cfg = software_cfg(port(go_app, "3.13", _no_pypi, source=go_source))
    assert cfg.count("tools = ") == 1
    assert "tools = ${golang1.23:location}/bin:${nodejs:location}/bin:${:base-tools}\n" in cfg
    assert "<= nodejs-" in cfg


def test_port_refuses_a_go_mod_newer_than_the_components(go_app, go_source):
    (go_source / "go.mod").write_text("go 1.99\n")
    with pytest.raises(PortError, match="1.99"):
        port(go_app, "3.13", _no_pypi, source=go_source)


def test_port_marks_a_prebuilt_binary_as_provisional(go_app):
    toml = go_app / "hop3.toml"
    toml.write_text(toml.read_text().replace('build = "make forge"', 'toolchain = "generic"'))
    release = port(go_app, "3.13", _no_pypi)
    assert release.prebuilt and release.eggs == []
    assert release.commands == ["bash scripts/download.sh"]
    assert any("provisional" in n for n in release.notes)
    cfg = software_cfg(release)
    assert "# Provisional: runs the binary" in cfg
    assert "golang" not in cfg and "./versions.cfg" not in cfg


def test_port_go_app_needs_its_source_to_be_told_apart(go_app):
    with pytest.raises(PortError, match="--source"):
        port(go_app, "3.13", _no_pypi)


def test_port_go_builds_with_slapos_go(go_app, go_source, tmp_path):
    release = port(go_app, "3.13", _no_pypi, source=go_source)
    assert release.go and release.eggs == [] and release.pins == []
    assert release.commands == ["bash scripts/download.sh", "make forge"]
    cfg = software_cfg(release)
    assert "  ../../component/golang/buildout.cfg\n" in cfg
    assert "tools = ${golang1.23:location}/bin:${:base-tools}\n" in cfg
    assert "./versions.cfg" not in cfg
    assert "eggs =\nsecrets" in cfg
    out = tmp_path / "out"
    out.mkdir()
    write(release, out)
    assert not (out / "versions.cfg").exists()


def test_port_multi_line_admin_create_becomes_a_script(go_app, go_source, tmp_path):
    release = port(go_app, "3.13", _no_pypi, source=go_source)
    assert release.processes["after-start"] == ["bash hop3-admin-create.sh"]
    cfg = software_cfg(release)
    assert "${HOP3_ADMIN_USER" not in cfg  # buildout would substitute it
    assert "  hop3-admin-create.sh " in cfg  # fetched with the recipe's files
    out = tmp_path / "out"
    out.mkdir()
    write(release, out)
    script = (out / "hop3" / "hop3-admin-create.sh").read_text()
    assert 'USER_NAME="${HOP3_ADMIN_USER:?not injected}"' in script  # as the recipe has it


def test_port_generated_secrets_keep_their_kind(go_app, go_source):
    release = port(go_app, "3.13", _no_pypi, source=go_source)
    assert {"TOKEN:hex", "JWT:urlsafe", "KEY:base64", "PLAIN", "SHORT:hex"} <= set(release.secrets)
    assert any(n.startswith("[env] ID:") for n in release.notes)  # uuid: not a kind we make
    assert any(n.startswith("[env] SHORT:") for n in release.notes)  # 32 bytes, not 8


def test_port_operator_email_has_a_default(go_app, go_source):
    release = port(go_app, "3.13", _no_pypi, source=go_source)
    assert "HOP3_ADMIN_EMAIL=admin@forge.invalid" in release.environment
    assert any("HOP3_ADMIN_EMAIL" in n for n in release.notes)


def test_port_computed_env_reads_the_admin(go_app, go_source):
    toml = go_app / "hop3.toml"
    toml.write_text(toml.read_text() + '\n[env.computed]\nADMIN_USERNAME = "${HOP3_ADMIN_USER}"\n')
    env = port(go_app, "3.13", _no_pypi, source=go_source).environment
    assert env.index("HOP3_ADMIN_USER=root") < env.index('ADMIN_USERNAME="$HOP3_ADMIN_USER"')
