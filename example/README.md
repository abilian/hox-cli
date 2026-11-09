# Example: 21 applications on one SlapOS node

[`hox.toml`](hox.toml) is the configuration with which we built and deployed 21 applications on one SlapOS compute node on Rapid.Space, in November 2026, for the HyperOpen-X project. All 21 passed their checks, on the backend and through Rapid.Space's frontend. We also tested each one by hand: at least a sign-in, a wrong password refused, and the app's real work.

- Three apps run on the **python-app stack**, with releases written by hand: mynij-proxy, Abilian SBE and AIPress24.
- Eighteen come from the **Hop3 catalog**, each release written by `hox port` from the app's Hop3 recipe onto the **hop3 stack**.

The releases and both stacks are in [abilian/slapos_abilian_edge](https://github.com/abilian/slapos_abilian_edge), branch `hop3-experiment` (`software/<name>/`, `stack/python-app/`, `stack/hop3/`).

## The applications

| Software | Application | Built from | Database |
|---|---|---|---|
| `mynij-proxy` | CORS proxy (Flask) | Python, by hand | |
| `abilian-sbe` | Abilian SBE, collaboration (Flask) | Python, by hand | PostgreSQL |
| `aipress24` | AIPress24 (Flask) | Python, by hand | PostgreSQL |
| `radicale` | Radicale, CalDAV and CardDAV | Python | |
| `bugsink` | Bugsink, error tracking | Python, with a worker | PostgreSQL |
| `isso` | Isso, comments | Python from source, Node for the JS | SQLite |
| `isso-venv` | the same, in a venv (`hox port --venv`) | Python, compiled packages from source | SQLite |
| `miniflux` | Miniflux, feed reader | Go | PostgreSQL |
| `gitea-source` | Gitea, Git forge | Go, from source | PostgreSQL |
| `forgejo-source` | Forgejo, Git forge | Go, from source | PostgreSQL |
| `vikunja-source` | Vikunja, to-do lists | Go and Node, from source | PostgreSQL |
| `mattermost-source` | Mattermost, team chat | Go and Node, from source | PostgreSQL |
| `uptime-kuma` | Uptime Kuma, monitoring | Node | SQLite |
| `paheko` | Paheko, association management | PHP | SQLite |
| `dolibarr` | Dolibarr, ERP and CRM | PHP | PostgreSQL |
| `limesurvey-deny` | LimeSurvey, surveys | PHP | PostgreSQL |
| `kanboard-deny` | Kanboard, kanban boards | PHP | MariaDB |
| `wordpress-deny` | WordPress | PHP | MariaDB |
| `matomo-deny` | Matomo, web analytics | PHP | MariaDB |
| `nextcloud-deny` | Nextcloud, files | PHP | MariaDB |
| `bookstack-current` | BookStack, wiki | PHP, with composer | MariaDB |

Every database runs inside the app's own partition. A name ending in `-source`, `-deny` or `-current` is a variant of the catalog's recipe:

- `-source` builds the app from the project's source release, where the golden recipe downloads a binary;
- `-deny` adds the list of paths nginx must refuse, which the app's `.htaccess` files protect under Apache;
- `-current` moves to a recent release of the app.

[`notes/02-hop3-compat.md`](../notes/02-hop3-compat.md) explains how a recipe becomes a release, what each variant changes, and what we learned on the way.

## The node

The node we used for the tests is a VM with 4 vCPUs, 8 GB of memory, 100 GB of disk and no swap, set up with 24 partitions. Memory was the limit:

- The running instances use from about 150 MB (Radicale) to 1.5 GB (AIPress24) each, 300 to 500 MB for most.
- Some builds need far more: Vikunja's frontend build peaks at 3.7 GB in one process, Mattermost's at 3.2 GB. Run such builds one at a time on a node this size.
- A first build on an empty node takes hours, since SlapOS builds the components (compilers, Node, PostgreSQL, PHP…) from source. They are shared afterwards, so a later app builds in minutes to half an hour.

## Reusing it

1. Set up a workspace: this file as `hox.toml` at the workspace root, and a clone of `abilian/slapos_abilian_edge` on branch `hop3-experiment` in `slapos/`.
2. Replace our values with yours: `[project] reference`, `[node] title` and `ssh`, and each `instance` name, since names belong to a project. The `ssh` line expects an `ssh_config` next to the file, for the jump host to the node.
3. Delete the tables of the apps you don't want, then deploy one:

```
hox status                 # reaches the master and the node?
hox deploy radicale        # setup, request, build, checks
hox instances              # what runs
```

To deploy from your own fork, change `repo`: the node fetches each release from `raw.githubusercontent.com/<repo>/<branch>/…`.

## Porting another Hop3 app

```
hox port path/to/hop3-catalog/apps/golden/<app> ./slapos
```

`hox port` writes `slapos/software/<app>/` and prints what it couldn't map. Commit and push the release, add a table like the ones here (pick a page for the checks that needs the app and the database, as the app's Hop3 healthcheck does), then `hox deploy <app>`. Before a deploy, `hox try-start <app>` on a test build finds in a minute what a failed deploy shows after 45. See [Porting Hop3 apps](../docs/src/guides/porting-hop3.md).

## Left out of this example

- **AIPress24's instance parameters** hold placeholders here (`change-me`, `example.org` addresses). The master stores parameters in clear, so keep real secrets out of shared files.
- **The SSH configuration** for our node and its jump host.
