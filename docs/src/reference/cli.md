# Commands

`SOFTWARE` is the name of a `[software.<name>]` table of `hox.toml`. It is optional when the file has only one. `NAME` is an instance's name.

Global options, anywhere on the command line: `--config PATH` (the `hox.toml` to read; by default the first one in the current directory or its parents) and `--debug` (show the traceback when a command fails). A failure prints one line with the master's, the node's or git's message, and exits with code 1.

Commands that change state are idempotent: running one twice is harmless. The exception is `hox node-certificate`, which issues a new certificate each time.

The [glossary](glossary.md) explains the SlapOS terms.

## Seeing

### `hox instances [-l|--long] [--json]`

`hox instances` lists the project's instances: name, status as the node sees it, memory (MB), frontend URL. `--long` adds their software in `hox.toml`, their release and their partition. See [Diagnosing problems](../guides/diagnosing.md#the-instances) for the statuses.

### `hox status [SOFTWARE|NAME] [--json]`

Without a name: the whole system (node, partitions, memory, disk, incomplete builds, failing promises, services down, software of `hox.toml` with no instance), then the instances. With a software or instance name: that one in detail, including why it isn't allocated and whether GitHub serves your branch head.

### `hox check [SOFTWARE]`

Runs the software's checks with curl on the node. Exit 1 if one fails.

### `hox logs {software,instance,app} [--software SOFTWARE] [-f|--follow] [-n|--lines N]`

Tails slapgrid's software log or instance log, or (`app`) the instance's service logs. Shows 50 lines unless `-n` says otherwise.

### `hox build-report [SOFTWARE]`

A Markdown report of the software's builds on the node, from the node's software logs (14 days kept): each run of each release URL, the longest parts, release and shared part sizes, disk use.

### `hox api get RELATIVE_URL [--view VIEW]`

Prints a document of the master's API, by relative URL, as JSON.

### `hox api search QUERY [--select FIELD]… [--limit N]`

Prints the results of an ERP5 catalog query as JSON. `--select` can be repeated (default: `title` and `relative_url`); `--limit` defaults to 100.

## Developing

### `hox rebuild [SOFTWARE] [--software-only] [--no-wait]`

Refuses an unpushed branch, waits until `raw.githubusercontent.com` serves the pushed files (`--no-wait` skips this), rebuilds the branch URL in place on the node, runs the instance's partition, and runs the checks, as [The development loop](../guides/dev-loop.md) describes. `--software-only` stops after the build. Exit 1 if a check fails.

### `hox try-start SOFTWARE [--timeout 300]`

Starts a test build's real processes on the node (from `/srv/hox-dev/SOFTWARE`), with their haproxy and a PostgreSQL of their own, in a temporary directory, without the master. Stops at the first failure, with the logs. `--timeout` is how long to wait for the app to answer, in seconds. See [Testing a build without the master](../guides/porting-hop3.md#testing-a-build-without-the-master).

## Deploying

### `hox deploy [SOFTWARE]`

Takes the branch URL from nothing on the master to an instance that passes the checks: runs `setup` and `request`, then waits for the allocation, the build and the checks. Refuses an unpushed branch. Run it again after a failure; [First deploy](../get-started/first-deploy.md) describes each step.

### `hox setup [SOFTWARE]`

The one-time panel work for the branch URL: opens the node's allocation scope; creates the Software Product with the software type, the release, and the Allocation Supply on the node with the (release × type) cell ticked; validates the supply; supplies the URL. Skips what exists.

### `hox release COMMIT [--software SOFTWARE]`

The same for a pushed commit's pinned URL (full SHA). Doesn't move the instance.

### `hox supply [SOFTWARE] [--pinned COMMIT] [--remove]`

Supplies the branch URL, or a pinned URL, on the node; `--remove` removes it.

### `hox request [SOFTWARE] [--name NAME] [--pinned COMMIT] [--replace] [--yes]`

Requests the instance (`instance` in `hox.toml`, or `--name`) on the node, with the branch URL or a pinned one, and with the parameters of `[software.<name>.parameters]` if there is that table. An existing instance gets its original request again. If it runs another URL, `--replace` destroys it (after confirmation, unless `--yes`) and requests the next name; then set `instance` in `hox.toml`, as [Releases and instances](../guides/releases.md) explains.

### `hox stop NAME`, `hox start NAME`

Stops an instance's processes and keeps its data, or starts them again.

### `hox destroy NAME [--yes]`

Destroys an instance, after confirmation unless `--yes`.

## Setting up a node

### `hox node-certificate`

Prints the compute node's reference, certificate and key as JSON. Creates the node if needed; otherwise revokes the node's certificate for a new one, which cuts off a node still configured with the old one. See [Setting up a node](../guides/new-node.md).

### `hox re6st-token`

Prints the node's re6st token: the same one each time, after 10 minutes or more the first time. Only the token goes to stdout.

## Offline tools

These need no `hox.toml`.

### `hox versions [LOCK_FILE] [--python 3.12]`

A buildout `[versions]` section from a project's `uv.lock` (default `./uv.lock`). See [Python dependencies](../guides/python-versions.md).

### `hox port RECIPE SLAPOS [--python 3.13] [--source DIR] [--venv]`

A SlapOS software release from a Hop3 recipe, written to `SLAPOS/software/<app id>/`. `SLAPOS` must be a checkout with `stack/hop3/`. Needs `uv` for `--source` and `--venv`. See [Porting Hop3 apps](../guides/porting-hop3.md).
