# Python dependencies

A SlapOS release installs a Python app's dependencies with buildout, pinned in a `[versions]` section. If the app is managed with uv, its `uv.lock` already has every pin. `hox versions` writes the section from it:

```
hox versions path/to/app/uv.lock --python 3.12 > versions-app.cfg
```

The lock file defaults to `./uv.lock` and the Python to 3.12. It needs no `hox.toml`.

## What it writes

- The root project's run-time dependencies: dev groups are left out, and markers are evaluated for Linux x86_64 and the given Python. Each is pinned as uv resolved it.
- A `:whl` pin for each package that PyPI has as a wheel the node can install: a pure-Python wheel, or a manylinux x86_64 wheel for that Python. Buildout then installs the wheel instead of building from source.
- Comments that list the packages to look at:
    - those installed from binary wheels: they bundle their C libraries, not SlapOS's, so a security fix in one of those comes only with a new release of the package;
    - those built by SlapOS's own stack for `slapos.cookbook` (cffi, lxml, pyyaml): they get no `:whl` pin, which would break that build;
    - those buildout has to build from their sdist: the ones with C or Rust code need their libraries and build tools in the release;
    - those it can't install at all (no sdist, and no wheel for the node): replace them with another extra, or with a SlapOS component.

Buildout's own tools (pip, setuptools, wheel, zc.buildout) are left out: their versions are SlapOS's, whatever the app asks for.

## Keep both in step

The app's `uv.lock` and the release's `versions.cfg` now pin the same versions twice. When you change one, regenerate the other.
