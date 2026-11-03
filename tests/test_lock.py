import pytest

from hox_cli.lock import LockError, Pin, render_versions, runtime_pins, versions_cfg

TAGS = {"pure": "py3-none-any", "binary": "cp312-cp312-manylinux_2_17_x86_64",
        "py311": "cp311-cp311-manylinux_2_17_x86_64", "mac": "cp312-cp312-macosx_11_0_arm64"}


def package(name, version="1.0", deps=(), sdist=True, wheels=("pure",), **extra):
    p = {"name": name, "version": version, "source": {"registry": "https://pypi.org/simple"},
         "dependencies": list(deps),
         "wheels": [{"url": f"https://files.pythonhosted.org/packages/x/{name.replace('-', '_')}-{version}-{TAGS[w]}.whl"}
                    for w in wheels]}
    if sdist:
        p["sdist"] = {"url": f"https://files.pythonhosted.org/packages/x/{name}-{version}.tar.gz"}
    return p | extra


LOCK = {"package": [
    package("app", source={"editable": "."}, deps=[
        {"name": "flask"},
        {"name": "orjson"},
        {"name": "setuptools"},
        {"name": "psycopg", "extra": ["binary"]},
        {"name": "pywin32", "marker": "sys_platform == 'win32'"},
        {"name": "typing-extensions", "marker": "python_full_version < '3.13'"},
        {"name": "numpy", "version": "2.0", "marker": "python_full_version < '3.13'"},
        {"name": "numpy", "version": "2.3", "marker": "python_full_version >= '3.13'"},
    ], **{"dev-dependencies": {"dev": [{"name": "pytest"}]}}),
    package("flask", deps=[{"name": "markupsafe"}]),
    package("markupsafe", wheels=("binary",)),
    package("orjson", wheels=("py311", "mac")),
    package("psycopg", **{"optional-dependencies": {
        "binary": [{"name": "psycopg-binary", "marker": "implementation_name != 'pypy'"}]}}),
    package("psycopg-binary", sdist=False, wheels=("py311",)),
    package("pywin32"),
    package("setuptools"),
    package("typing-extensions"),
    package("numpy", "2.0", wheels=("binary",)),
    package("numpy", "2.3", wheels=("binary",)),
    package("pytest"),
]}


def test_runtime_pins():
    assert runtime_pins(LOCK, "3.12") == [
        Pin("flask", "1.0", sdist=True, wheel="pure"),
        Pin("markupsafe", "1.0", sdist=True, wheel="binary"),
        Pin("numpy", "2.0", sdist=True, wheel="binary"),
        Pin("orjson", "1.0", sdist=True, wheel=None),
        Pin("psycopg", "1.0", sdist=True, wheel="pure"),
        Pin("psycopg-binary", "1.0", sdist=False, wheel=None),
        Pin("typing-extensions", "1.0", sdist=True, wheel="pure"),
    ]


def test_runtime_pins_follow_markers():
    names = {p.name: p.version for p in runtime_pins(LOCK, "3.13.1")}
    assert "typing-extensions" not in names
    assert names["numpy"] == "2.3"


def test_runtime_pins_need_one_root():
    with pytest.raises(LockError, match="found 0"):
        runtime_pins({"package": LOCK["package"][1:]}, "3.12")


def test_versions_cfg(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text("""
[[package]]
name = "app"
version = "0.1"
source = { editable = "." }
dependencies = [{ name = "flask" }, { name = "bcrypt" }, { name = "psycopg-c" }, { name = "psycopg-binary" }]

[[package]]
name = "flask"
version = "3.1.2"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/flask-3.1.2.tar.gz" }
wheels = [{ url = "https://x/flask-3.1.2-py3-none-any.whl" }]

[[package]]
name = "bcrypt"
version = "4.3.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/bcrypt-4.3.0.tar.gz" }
wheels = [{ url = "https://x/bcrypt-4.3.0-cp39-abi3-manylinux_2_28_x86_64.whl" }]

[[package]]
name = "psycopg-c"
version = "3.3.6"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/psycopg_c-3.3.6.tar.gz" }

[[package]]
name = "psycopg-binary"
version = "3.3.6"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://x/psycopg_binary-3.3.6-cp311-cp311-manylinux_2_28_x86_64.whl" }]
""")
    cfg = versions_cfg(lock, "3.12")
    assert cfg.endswith("""
[versions]
bcrypt         = 4.3.0:whl
flask          = 3.1.2:whl
psycopg-binary = 3.3.6
psycopg-c      = 3.3.6
""")
    assert "SlapOS's: a security fix in one comes with a new release of the package.\n#   bcrypt\n" in cfg
    assert "libraries and build tools in the build:\n#   psycopg-c\n" in cfg
    assert "(another extra, or a SlapOS component):\n#   psycopg-binary\n" in cfg


def test_stack_built_eggs_have_no_wheel_pin():
    cfg = render_versions([Pin("PyYAML", "6.0.3", sdist=True, wheel="binary"),
                           Pin("bcrypt", "4.3.0", sdist=True, wheel="binary")], "x", "3.13")
    assert cfg.endswith("[versions]\nPyYAML = 6.0.3\nbcrypt = 4.3.0:whl\n")
    assert "bundled, not\n# SlapOS's: a security fix in one comes with a new release of the package.\n#   bcrypt\n" in cfg
    assert "(slapos.cookbook), from source, with its recipe:\n#   PyYAML\n" in cfg
