# Porting Hop3 apps to SlapOS

A Hop3 recipe is an app's directory in the Hop3 catalog, with a `hop3.toml` and scripts. `hox port` translates the recipe into a SlapOS software release on the `hop3-experiment` branch of `slapos_abilian_edge`. It writes `software/<id>/software.cfg` and copies the recipe's files into `hop3/` beside it, leaving out the catalog's own files (`catalog.toml`, readmes, icon, screenshots, `check.py`). `software.cfg` lists each copied file with an md5.

## What a recipe becomes

`hox port` maps the recipe's sections onto options of a shared stack (`stack/hop3`, built on `stack/python-app`), so that each release is a short file with little code beyond the stack:

- the toolchain becomes the components to build: PHP and nginx, Node, Go, Python eggs (pinned in a `versions.cfg`) or a venv, plus composer when the build runs it
- `[build]` commands, such as `download.sh`, `composer install` or `npm ci`, become build steps run in the app's directory, with the stack's tools on `PATH`
- `[run]` becomes supervised processes: `before-run` the `release` step, `start` the `web` service, each worker a separate service
- `[env]`, `[admin]` and generated secrets become environment lines, and secrets the stack generates once and keeps; `[admin] create` becomes an `after-start` step, run once
- the `postgres` and `mysql` addons become a PostgreSQL or a MariaDB 11.8 inside the partition, with the variables Hop3 gives (`DATABASE_URL` and `PG*`, `MYSQL_*`)
- `[[volumes]]` become directories under `srv/volumes`, linked into the copy of the build the processes run from
- `[proxy] deny`, for a PHP app, becomes 404 rules in the stack's nginx
- `[healthcheck]` becomes the node's promises, on the backend and the frontend
- what Hop3 sets with `hop3 config set` becomes instance parameters (`[software.<name>.parameters]` in `hox.toml`)

Every release also gets the environment Hop3 gives an app (`HOST_NAME`, `HOP3_PUBLIC_URL`) and `HOP3_BACKEND_URL`, the backend address the node's health promise uses: an app that checks the `Host` header has to trust it.

## What the instance does

When you deploy, the node builds the release (`slapos node software`). The shared instance template then sets up the partition:

1. the database, secrets and environment
2. haproxy on the partition's public IPv6, in front of the app on `127.0.0.1`
3. a request to Rapid.Space's shared frontend, which publishes the app's https address after the instance's first run
4. the `release` step (migrations, for instance), which runs only once that address is there, since release scripts write it into the app's configuration; it runs again when the parameters, the build or the address change
5. the long-running services, which wait for the release step to have run, then `after-start` (creating the admin account), retried until it succeeds, once per instance

## How hox drives it

- `hox port` translates the recipe
- `hox try-start` runs a test build's processes on the node, without the master
- `hox deploy` and `hox rebuild` work through the Rapid.Space master and the node
- `hox check`, `hox status` and `hox instances` read the result

Golden recipes stay as Hop3 has them. Wherever SlapOS needs a change, such as a deny list, a newer version or a build from source, the change goes into a catalog variant (`<app>-deny`, `-source`, `-current`).

## Limits

- Toolchains: python, go, node, php, and generic. A generic recipe fetches a prebuilt binary; the port is provisional and says so in `software.cfg`, until a variant of the recipe builds the app from source. Only Hop3's local builder: Docker and Nix builds are refused.
- A Go recipe doesn't name a toolchain: `hox port --source` with an unpacked copy of the app finds the `go.mod` and picks the oldest SlapOS Go component it allows (1.23, 1.24, 1.26). A Python app with no `requirements.txt` needs `--source` too: uv resolves the app's dependencies on the day of the port.
- Build and run commands must be one line each, without `${…}`, which buildout would split or substitute. `[env.computed]` values have their `${NAME}` rewritten as `$NAME`; a multi-line `[admin] create` becomes a script next to the recipe's files.
- Not mapped: `[probe]`, `[build]`/`[run]` `packages`, `[proxy]` other than a PHP app's `deny`, addons other than postgres and mysql, and generated secrets of kinds other than hex, base64, urlsafe and password. Ignored, since they don't apply on SlapOS: `[port]`, `[domains]`, `[deploy]`, `[backup]`, `[limits]`, `[contexts]`, `[test]`. `hox port` prints what it didn't map after the files it wrote: read that list before deploying.
- `hox port` overwrites only a release that it wrote itself. A recipe whose id clashes with a hand-written release is refused.

## Where the details are

- `../../slapos/stack/hop3/README.md`: the section-by-section mapping table, on the `hop3-experiment` branch. Its "Not mapped yet" paragraph predates composer and the mysql addon.
- `../../notes/porting-from-hop3/`: why the port works this way, the catalog apps ported so far, and the lessons learned.
- `01-spec.md`, 1.3: the `hox port` and `hox try-start` commands.
