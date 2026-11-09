# hox-cli

`hox` is a command-line tool for deploying your applications on [SlapOS](https://slapos.nexedi.com), the open-source cloud system made by Nexedi, and for working on them once they run. It works with [Rapid.Space](https://rapid.space)'s public SlapOS cloud and a compute node of your own.

You describe your applications in one configuration file, `hox.toml`. From then on, deploying, updating and checking them takes one command each, with no web forms to fill in:

- **Deploy in one command.** `hox deploy` does the web panel's setup for you, asks the master for an instance, waits for the node to build and start it, then checks that the application answers.
- **Update after each change.** Push to Git, then `hox rebuild`: it rebuilds the application on the node, redeploys it and runs your checks.
- **Know what's going on.** `hox status` gathers what the master, the node and GitHub know: whether a build failed and why, which processes are down, why an instance hasn't started.
- **Manage instances by name.** Stop, start or destroy an instance by name alone, where SlapOS's `slapos` command needs its release URL, type and node repeated exactly.
- **Bring Hop3 apps to SlapOS.** `hox port` turns an app of the [Hop3](https://hop3.cloud) catalog into a SlapOS software release.

## Install

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/abilian/hox-cli
uv tool install --editable ./hox-cli
hox --help
```

## Quick start

1. Get a SlapOS client certificate for your Rapid.Space account (`slapos configure client`), and root SSH access to your compute node.
2. Copy `hox.example.toml` to `hox.toml` at the root of your workspace, and fill in your project, node and software release.
3. Check that `hox` reaches the master and the node, then deploy:

```
hox status
hox deploy my-app
```

## Commands

| | |
|---|---|
| Seeing | `instances`, `status`, `check`, `logs`, `build-report`, `api get`, `api search` |
| Developing | `rebuild`, `try-start` |
| Deploying | `deploy`, `setup`, `release`, `supply`, `request`, `stop`, `start`, `destroy` |
| Setting up a node | `node-certificate`, `re6st-token` |
| Offline tools | `versions` (buildout pins from a `uv.lock`), `port` (a SlapOS release from a Hop3 recipe) |

`hox COMMAND --help` describes each one.

## Documentation

The documentation is in [`docs/src/`](docs/src/index.md): getting started, guides, the [command reference](docs/src/reference/cli.md), the [`hox.toml` reference](docs/src/reference/config.md) and a [glossary](docs/src/reference/glossary.md) of SlapOS terms. To read it as a site: `cd docs && make serve`.

The design notes are in [`notes/`](notes/).

## Development

```
uv sync
make test      # all tests; tests/a_unit, tests/b_integration, tests/c_e2e for one tier
make lint      # ruff, ty, pyrefly, mypy
```

The tests need no network, master or node. See [Contributing](docs/src/developers/contributing.md).

## Licence

Apache 2.0: see [LICENSE](LICENSE). `hox` was written by [Abilian](https://abilian.com) for [HyperOpen-X](https://lab.abilian.com/Projects/HOX/), a French public research project on an open, sovereign cloud.
