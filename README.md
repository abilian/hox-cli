# hox-cli

`hox` drives our SlapOS work on Rapid.Space (master, project, compute node) from the terminal, so that building, deploying and checking a software release never needs the web panel. Spec: `notes/01-spec.md`.

## Install and run

```
uv tool install --editable ./hox-cli      # from the workspace root
hox status                                # reads ../hox.toml (searched upward from the current directory)
```

Without installing: `uv run --project hox-cli hox status`.

## Commands so far

- `hox status [SOFTWARE] [--json]`: instance, partition (or why it isn't allocated), connection parameters, frontends, builds on the node, failing promises, whether GitHub serves the pushed commit.
- `hox rebuild [SOFTWARE] [--software-only] [--no-wait]`: the dev loop. Refuses an unpushed branch, waits for raw.githubusercontent.com to serve the pushed files, rebuilds the branch URL in place on the node, redeploys, runs the checks.
- `hox check [SOFTWARE]`: the checks of hox.toml, with curl on the node. Exit 1 if one fails.
- `hox logs {software,instance,app} [--software NAME] [-f] [-n N]`: tail slapgrid's logs or the instance's service logs.
- `hox api get RELATIVE_URL [--view VIEW]`, `hox api search QUERY [--select FIELD …] [--limit N]`: raw read-only access to the master's API.

## Tests

```
cd hox-cli && uv run pytest
```
