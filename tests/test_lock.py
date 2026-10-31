import pytest

from hox_cli.lock import LockError, Pin, runtime_pins, versions_cfg

WHEEL = "https://files.pythonhosted.org/packages/x/{}-py3-none-any.whl"
BINARY = "https://files.pythonhosted.org/packages/x/{}-cp312-cp312-manylinux_2_17_x86_64.whl"


def package(name, version="1.0", deps=(), sdist=True, wheels=("pure",), **extra):
    p = {"name": name, "version": version, "source": {"registry": "https://pypi.org/simple"},
         "dependencies": list(deps),
         "wheels": [{"url": (WHEEL if w == "pure" else BINARY).format(name)} for w in wheels]}
    if sdist:
        p["sdist"] = {"url": f"https://files.pythonhosted.org/packages/x/{name}-{version}.tar.gz"}
    return p | extra


LOCK = {"package": [
    package("app", source={"editable": "."}, deps=[
        {"name": "flask"},
        {"name": "psycopg", "extra": ["binary"]},
        {"name": "pywin32", "marker": "sys_platform == 'win32'"},
        {"name": "typing-extensions", "marker": "python_full_version < '3.13'"},
        {"name": "numpy", "version": "2.0", "marker": "python_full_version < '3.13'"},
        {"name": "numpy", "version": "2.3", "marker": "python_full_version >= '3.13'"},
    ], **{"dev-dependencies": {"dev": [{"name": "pytest"}]}}),
    package("flask", deps=[{"name": "markupsafe"}]),
    package("markupsafe", wheels=("binary",)),
    package("psycopg", **{"optional-dependencies": {
        "binary": [{"name": "psycopg-binary", "marker": "implementation_name != 'pypy'"}]}}),
    package("psycopg-binary", sdist=False, wheels=("binary",)),
    package("pywin32"),
    package("typing-extensions"),
    package("numpy", "2.0", wheels=("binary",)),
    package("numpy", "2.3", wheels=("binary",)),
    package("pytest"),
]}


def test_runtime_pins():
    assert runtime_pins(LOCK, "3.12") == [
        Pin("flask", "1.0", sdist=True, pure=True),
        Pin("markupsafe", "1.0", sdist=True, pure=False),
        Pin("numpy", "2.0", sdist=True, pure=False),
        Pin("psycopg", "1.0", sdist=True, pure=True),
        Pin("psycopg-binary", "1.0", sdist=False, pure=False),
        Pin("typing-extensions", "1.0", sdist=True, pure=True),
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
dependencies = [{ name = "flask" }, { name = "bcrypt" }, { name = "psycopg-binary" }]

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
name = "psycopg-binary"
version = "3.3.6"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://x/psycopg_binary-3.3.6-cp312-cp312-manylinux_2_28_x86_64.whl" }]
""")
    cfg = versions_cfg(lock, "3.12")
    assert cfg.endswith("""
[versions]
bcrypt         = 4.3.0
flask          = 3.1.2:whl
psycopg-binary = 3.3.6
""")
    assert "#   bcrypt\n" in cfg
    assert "can't install them" in cfg and "#   psycopg-binary\n" in cfg
