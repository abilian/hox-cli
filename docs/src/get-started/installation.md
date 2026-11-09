# Installation

## Requirements

- Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).
- A SlapOS client certificate for your Rapid.Space account: `~/.slapos/client.crt` and `client.key`, as `slapos configure client` writes them.
- Root SSH access to your compute node. `hox` runs `ssh` with the arguments you give it, so a jump host or an SSH config file works as usual.
- `git`, and push access to the GitHub repository your software releases come from. The node fetches releases from `raw.githubusercontent.com`.

`hox versions` and `hox port` need only Python and uv: they work offline (apart from PyPI lookups) and need no configuration.

## Install

```
git clone https://github.com/abilian/hox-cli
uv tool install --editable ./hox-cli
hox --help
```

`--editable` keeps the installed command in step with the checkout. To run it without installing:

```
uv run --project hox-cli hox --help
```

## Global options

These go anywhere on the command line:

- `--config PATH`: the `hox.toml` to read. By default, `hox` reads the first `hox.toml` in the current directory or its parents.
- `--debug`: show the Python traceback when a command fails.

When a step fails, `hox` prints one line with the message of the master, the node or git, and exits with code 1.

Next: [Configuration](configuration.md).
