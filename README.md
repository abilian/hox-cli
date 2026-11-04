# hox-cli

`hox` drives our SlapOS work on Rapid.Space (master, project, compute node) from the terminal, so that building, deploying and checking a software release never needs the web panel. Spec: `notes/01-spec.md`.

## Install and run

```
uv tool install --editable ./hox-cli      # from the workspace root
hox status                                # reads ../hox.toml (searched upward from the current directory)
```

Without installing: `uv run --project hox-cli hox status`.

## Configuration

`hox` reads the first `hox.toml` in the current directory or its parents: the HOX workspace keeps its own at the root, next to the checkouts it names. `hox.example.toml` is a commented copy of it (a test parses it, so it stays valid); start from it to set up another workspace.

## Commands

- `hox status [SOFTWARE] [--json]`: instance, partition (or why it isn't allocated), connection parameters, frontends, builds on the node, failing promises, whether GitHub serves the pushed commit.
- `hox rebuild [SOFTWARE] [--software-only] [--no-wait]`: the dev loop. Refuses an unpushed branch, waits for raw.githubusercontent.com to serve the pushed files, rebuilds the branch URL in place on the node, redeploys, runs the checks.
- `hox try-start SOFTWARE [--timeout 300]`: start a test build's real processes on the node (from `/srv/hox-dev/<software>`, as `run-build.sh` left it), in a temporary directory with their haproxy and a PostgreSQL of their own, and stop at the first failure with the logs. It finds in a minute what a deploy finds after its checks time out. No frontend or instance parameters, so it doesn't replace testing through the frontend.
- `hox build-report [SOFTWARE]`: Markdown report of the software's builds on the node, from its software logs (14 days kept): each run of each release URL (start, end, duration, result), the longest parts, release and shared part sizes, disk used.
- `hox versions [LOCK_FILE] [--python 3.12]`: a buildout `[versions]` section from a project's `uv.lock` (default `./uv.lock`): its run-time dependencies with their markers evaluated for Linux x86_64, as `:whl` when there is a wheel the node can install (pure, or manylinux x86_64 for that Python), and comments listing those from binary wheels (their C libraries bundled), those buildout must build from their sdist, and those it can't install at all. Leaves out buildout's own tools (pip, setuptools, wheel, zc.buildout). Needs no `hox.toml`.
- `hox port RECIPE SLAPOS [--python 3.13]`: a SlapOS software release from a Hop3 recipe (a catalog app's directory, with its `hop3.toml`), on the hop3 stack of `slapos_abilian_edge` (branch `hop3-experiment`). Writes `SLAPOS/software/<app id>/`: a `software.cfg`, a `versions.cfg` from the recipe's `requirements.txt` (wheels looked up on PyPI), and the recipe's files in `hop3/`, leaving out the catalog's overlay (`catalog.toml`, readmes, icon, screenshots, `check.py`). Lists what it couldn't map; refuses what the stack can't do yet (other toolchains than Python, other builders than local, apps installed from their source). Needs no `hox.toml`.
- `hox check [SOFTWARE]`: the checks of hox.toml, with curl on the node. Exit 1 if one fails.
- `hox logs {software,instance,app} [--software NAME] [-f] [-n N]`: tail slapgrid's logs or the instance's service logs.
- `hox supply [SOFTWARE] [--pinned COMMIT] [--remove]`: supply (or remove) the branch or a pinned URL on our node.
- `hox request [SOFTWARE] [--name NAME] [--pinned COMMIT] [--replace] [--yes]`: request the instance; re-requesting re-sends the original request, with the parameters of `[software.<name>.parameters]` in hox.toml if it has that table. `--replace` destroys an instance on another URL and requests the next name.
- `hox destroy NAME [--yes]`: destroy an instance.
- `hox deploy [SOFTWARE]`: from nothing on the master to an instance that passes its checks: `setup`, `request`, then waits for the allocation, the node's build (hours for SBE on a cold cache) and the checks (frontends take a while). Run it again after a failure.
- `hox node-certificate`: print the compute node's (`[node] title`) reference, certificate and key as JSON, for `node-deployer/deploy.py`. Creates the node if needed; otherwise revokes its certificate for a new one, which cuts off a node still configured with the old one.
- `hox re6st-token`: print the node's re6st token (the same one each time; 10+ minutes the first time). Only the token goes to stdout.
- `hox setup [SOFTWARE]`: the one-time panel work for the branch URL: node allocation scope open, Software Product, software type, release, Allocation Supply on our node with the cell ticked, validated; then supplies the URL. Skips what exists.
- `hox release COMMIT [--software NAME]`: the same for a pushed commit's pinned URL (full SHA). Doesn't move the instance.
- `hox api get RELATIVE_URL [--view VIEW]`, `hox api search QUERY [--select FIELD …] [--limit N]`: raw read-only access to the master's API.

## Tests

```
cd hox-cli && uv run pytest
```
