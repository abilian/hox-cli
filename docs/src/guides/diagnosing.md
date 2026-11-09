# Diagnosing problems

All the commands on this page are read-only.

## The whole system

```
hox status
```

It shows:

- the node's uptime, partitions used and free, memory and disk;
- the builds that aren't complete, with their last error;
- the promises that failed in the last instance run;
- the services that are down, by instance;
- the software of `hox.toml` with no instance;
- then the list of instances.

## The instances

```
hox instances        # INSTANCE, STATUS, MB, FRONTEND
hox instances -l     # adds SOFTWARE, RELEASE, PARTITION
hox instances --json
```

The status comes from the node:

| Status | Meaning |
|---|---|
| `up` | All of its processes run. Only `hox check` tells whether the app answers. |
| `N down` | N of its services are down. |
| `not running` | None of them run, as while the node boots. |
| `no services` | The partition has no services. |
| `not allocated` | The master hasn't given it a partition: `hox status NAME` says why. |
| `stopped`, `being destroyed` | As requested. |
| `unknown` | The node is unreachable. The list still prints, from the master. |

A service counts as down if supervisord shows it in another state than `RUNNING`, `EXITED` or `STOPPED`, or if it is a watched (`-on-watch`) service that isn't `RUNNING`. Supervisord restarts nothing and shows no exit code, so a crash reads `EXITED`. A service that exits once its work is done belongs in the partition's `etc/run`; if your release leaves one in `etc/service`, name it in `one_shot` of its `[software.<name>]`, or it counts as down.

Of the connection parameters, `hox instances` reads only `url`: the others can hold credentials.

## One software in detail

```
hox status my-app       # by its name in hox.toml
hox status my-app-1     # or by its instance's name
```

- **instance**: requested state, release (named after `hox.toml`: "my-app branch", "my-app @ 0123456ab"), software type, partition;
- **diagnosis**: why an instance isn't allocated. The usual cause is a release that isn't ticked in a validated Allocation Supply of the project: the instance then stays without a partition, with no error anywhere;
- **services**: how many run, which are down, their memory;
- **news**: the master's news text for the instance;
- **connection parameters** and **frontends**, allocated or not;
- **software**: each release URL of this software, as the master and the node see it: built, building, or failed with buildout's last error;
- **promises** that failed in the last instance run;
- **github**: whether your local branch head is pushed, and whether `raw.githubusercontent.com` already serves it.

`--json` prints all of it for scripts and agents.

## Build times and disk use

```
hox build-report my-app > build-report.md
```

`hox build-report` writes a Markdown report from the node's software logs, which keep 14 days, rotated ones included. It has:

- each build run of each of the software's release URLs, with its start, end, duration and result;
- the parts that took longest;
- the sizes of the release directory and of the shared parts;
- the node's disk use, CPUs and `make` jobs.

## The master's API

```
hox api search 'portal_type:"Instance Tree" AND title:="my-app-1"' --select title --select slap_state
hox api get instance_tree_module/20260101.001-0A1B2C3D --view view
```

These give raw, read-only access to the master's API, printed as JSON: an ERP5 catalog query with the columns to return, or a document by its relative URL. Use them when `hox status` doesn't answer your question.
