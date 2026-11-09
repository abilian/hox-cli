# Architecture

`hox` is a small Python package (`src/hox_cli/`) with three dependencies: [cyclopts](https://cyclopts.readthedocs.io) for the command line, [httpx2](https://pypi.org/project/httpx2/) for HTTP with the client certificate, and [packaging](https://packaging.pypa.io) for requirements and wheel tags. It doesn't depend on `slapos.core`: everything it reads from the master is available as JSON from the API.

## Boundaries

Each remote boundary has a module and an exception type. `cli.py` turns those exceptions into a one-line message and exit code 1.

| Module | Talks to | Exception |
|---|---|---|
| `config.py` | `hox.toml`, parsed once into frozen dataclasses | `ConfigError` |
| `master.py` | the master: `Slap` (slap protocol) and `Api` (hateoas reads, form submits) | `MasterError` |
| `node.py` | the node over SSH, and local git checkouts | `NodeError` |
| `dev.py` | git and GitHub: pushed? served? Also the checks | `DevError` |
| `lock.py` | a `uv.lock` | `LockError` |
| `port.py` | a Hop3 recipe, PyPI, uv | `PortError` |

The slap protocol needs only four kinds of marshalled value, so `master.marshal` replaces slapos.core's xml-marshaller in a few lines, tested against the output of `slapos.core` 1.26.1.

Commands to the node are fixed strings built from configuration values with `shlex.quote`, appended to `[node] ssh`.

## Commands

`cli.py` has one function per command, and the output formatting. The work is in modules built on the boundaries:

- `status.py`: what the master, the node and GitHub say, for `status` and `instances`;
- `panel.py`: the panel's forms, for `setup`, `release`, `node-certificate` and `re6st-token`. Every step looks first and acts only when what it needs is missing;
- `deploy.py`: the waits of `deploy`, after `setup` and `request`;
- `report.py`: `build-report`, from the node's logs;
- `try_start.py`: `try-start`. This module isn't imported for its functions: its source is sent over SSH and run by the node's Python, so it uses only the standard library.

`port.py` maps a recipe to a `Release` one `hop3.toml` section at a time (a mapper per section), then renders it into `software.cfg` and `versions.cfg`.
