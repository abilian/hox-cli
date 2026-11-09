# Porting Hop3 apps

A [Hop3](https://hop3.cloud) recipe is an app's directory in the Hop3 catalog, with a `hop3.toml` and scripts. `hox port` translates a recipe into a SlapOS software release on the **hop3 stack**, which does the work common to every port. The stack is in [`stack/hop3/`](https://github.com/abilian/slapos_abilian_edge/tree/hop3-experiment/stack/hop3) of Abilian's SlapOS repository, branch `hop3-experiment`, built on `stack/python-app/`. Give `hox port` a recipe and a checkout of that branch:

```
hox port ~/hop3-catalog/apps/golden/radicale ./slapos
```

It writes `slapos/software/radicale/`:

- `software.cfg`, which extends the stack and the components the app needs;
- `hop3/`, the recipe's files, without the catalog's own (`catalog.toml`, readmes, icon, screenshots, `check.py`); `software.cfg` lists each file with an md5;
- for a Python app, `versions.cfg`: the recipe's `requirements.txt`, pinned for buildout, with the wheels PyPI has for the node.

Then it prints what it couldn't map. Read that list before deploying.

The generated `software.cfg` says it comes from `hox port`: change the recipe and port it again rather than edit it. `hox port` overwrites only a release it wrote itself, and refuses a recipe whose id is already taken by another release.

## What a recipe becomes

| Hop3 | SlapOS release |
|---|---|
| toolchain | the components to build: Python eggs (or a venv), Go, Node, PHP and nginx, composer when the build runs it |
| `[build]` commands (`download.sh`, `npm ci`, …) | build steps, run in the app's directory with the stack's tools on `PATH` |
| `[run] before-run` | the `release` step: migrations and the like |
| `[run] start`, `[run.workers]` | the `web` service, and a service per worker |
| `[admin] create` | an `after-start` step, run once |
| `[env]`, `[env.computed]`, `[admin]` | environment lines |
| generated secrets, the admin password | secrets the stack generates once and keeps |
| `[[addons]] postgres` | PostgreSQL in the partition, with `DATABASE_URL` and `PG*` |
| `[[addons]] mysql` | MariaDB 11.8 in the partition, with `MYSQL_*` |
| `[[volumes]]` | directories under `srv/volumes`, linked into the copy of the build the processes run from |
| `[proxy] deny`, for a PHP app | 404 rules in the stack's nginx |
| `[healthcheck]` | the node's promises, on the backend and the frontend |
| `hop3 config set` | instance parameters (`[software.<name>.parameters]` in `hox.toml`) |

Every release also gets the variables Hop3 gives an app (`HOST_NAME`, `HOP3_PUBLIC_URL`), and `HOP3_BACKEND_URL`, the backend address the node's health promise uses: an app that checks the `Host` header has to trust it.

## What the instance does

The node builds the release. The stack's instance profile then sets up the partition:

1. the database, the secrets and the environment;
2. haproxy on the partition's public IPv6, in front of the app on `127.0.0.1`;
3. a request to Rapid.Space's shared frontend, which publishes the app's https address after the instance's first run;
4. the `release` step, once that address is known, since release scripts often write it into the app's configuration; it runs again when the parameters, the build or the address change;
5. the long-running services, which wait for the release step, then `after-start`, retried until it succeeds.

## Options and limits

- **Toolchains**: python, go, node, php, and generic. A generic recipe fetches a prebuilt binary: the port is provisional, says so in `software.cfg`, and should give way to a recipe that builds the app from source. Only Hop3's local builder: Docker and Nix builds are refused.
- **`--python 3.13`**: the release's Python (3.13 by default).
- **`--source DIR`**: an unpacked copy of what the recipe fetches. A Python app without a `requirements.txt` needs it, for the app's name and dependencies, which `uv pip compile` resolves on the day of the port. A Go recipe needs it too, since it doesn't name a toolchain: `hox port` finds the `go.mod` and picks the oldest Go component that the `go.mod` allows (1.23, 1.24 or 1.26).
- **`--venv`**: installs a Python app with pip in a dedicated venv, where buildout would install eggs. Packages with compiled code are built from their sdists, with hashes in the pins. The release goes to `software/<id>-venv/`, with a `requirements.txt` in place of `versions.cfg`.
- **Commands** must be one line each, without `${…}`, which buildout would split or substitute. `[env.computed]` values have their `${NAME}` rewritten as `$NAME`; a multi-line `[admin] create` becomes a script next to the recipe's files.
- **Not mapped**: `[probe]`, `[build]`/`[run]` `packages`, `[proxy]` other than a PHP app's `deny`, addons other than postgres and mysql, and kinds of generated secret other than hex, base64, urlsafe and password. Ignored, since they don't apply on SlapOS: `[port]`, `[domains]`, `[deploy]`, `[backup]`, `[limits]`, `[contexts]`, `[test]`.

## Deploying a port

Commit and push the release, add a `[software.<id>]` table to `hox.toml` with the branch and path, then [`hox deploy`](../get-started/first-deploy.md).

## Testing a build without the master

A deploy goes through GitHub and the master. A release that fails to start shows up only when the checks time out, about 45 minutes later. `hox try-start` shortens that loop to about a minute:

```
hox try-start radicale --timeout 300
```

It starts the real processes of a test build, as `slapsoft`, in a temporary directory, with the release's own haproxy and a dedicated PostgreSQL. It stops at the first failure (a supervisord restart counts as one, and so does no answer before the timeout) and prints the logs. It needs nothing from the master, so there is no frontend and no instance parameters: it doesn't replace a test through the frontend.

The test build is one that a script left in `/srv/hox-dev/<name>` on the node, from a copy of your unpushed tree, using the node's shared parts so that only what changed is rebuilt. `hox` doesn't install that script: set it up on your node first.
