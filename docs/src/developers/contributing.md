# Contributing

## Setup

```
git clone https://git.sr.ht/~sfermigier/hox-cli
cd hox-cli
uv sync
uv run hox --help
```

## Tests and checks

```
make test                                    # uv run pytest
uv run pytest tests/a_unit                   # one tier
uv run pytest tests/a_unit/test_port.py -k venv   # a subset
make lint                                    # ruff, ty, pyrefly, mypy
make format                                  # ruff format and fixes
```

Tests are in three tiers:

- `tests/a_unit/`: one module at a time, with the master, the node, PyPI and uv faked; one `test_<module>.py` per module;
- `tests/b_integration/`: real git repositories, and `cli.py`'s commands run through config, status and master with only the master and node faked;
- `tests/c_e2e/`: the installed `hox` command run as a process, offline.

`tests/conftest.py` holds the shared config and fixture loaders, `tests/fakes.py` the fake master and node that several tiers use.

The tests need no network, master or node:

- the master's answers are API replies recorded on the real master (`tests/fixtures/`), served with `httpx2.MockTransport`;
- the node is a fake: `Node` takes the functions it runs commands with as arguments;
- the log parsers run on recorded logs;
- command tests replace `cli`'s helpers (`_config`, `_api`, `_node`, `Slap.connect`) with pytest's `monkeypatch`;
- `hox port` tests write small recipes, and pass PyPI, uv and source trees in as functions or directories.

There is no test against a live master or node. After a change that touches them, run `hox status` and `hox api search` on a real project.

`hox.example.toml` is parsed by a test: keep it valid when you change the configuration.

## Documentation

The site is built with [Zensical](https://zensical.org) from `docs/src/`:

```
cd docs
make serve     # local server, rebuilds on change
make build     # into docs/site/
make deploy    # build, then deploy with Hop3 (docs/hop3.toml)
```

When a command's behaviour or options change, update its entry in `README.md` and in `docs/src/reference/cli.md`.
