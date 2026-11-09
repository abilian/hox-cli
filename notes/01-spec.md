# hox-cli: specification

`hox` drives our SlapOS work on Rapid.Space from the terminal. Like the `slapos` client, it supplies software releases and requests instances. To stop, start or destroy an instance, you only give its name: `hox` resends the original request for you. It also does what the client can't: the work we did by hand in the panel (panel.rapid.space) and the commands we ran over SSH on our compute node. The goal is that building, deploying and checking a software release never needs the web panel.

The context lives in the workspace (`..` from `hox-cli/`):

- `notes/runbooks/own-slapos-node-plan.md`: how the node was set up and what we learned, 2026-10-26 → 29.
- `node-deployer/`: pyinfra deploy of the node itself, out of scope here.
- `notes/porting-from-hop3/`: the design behind `hox port`, `/srv/hox-dev/` and `hox try-start` (2026-11-03 →), with the lessons learned. 1.3 describes the commands; the reasons are there.

This note started as the spec written before the code (2026-10-29). It has since been kept in step with the code: it describes what `hox` does, and flags what isn't built.

## 1. Functional spec

### 1.1 Users and setting

One developer (or a coding agent) on a laptop, with:

- a SlapOS client certificate for their account (`~/.slapos/client.crt`, `client.key`, from `slapos configure client`);
- root SSH to the compute node (through `vigie-a`, as in `node-deployer/ssh_config`);
- push access to the GitHub repos the software releases come from;
- `uv` on the PATH, for `hox port`.

The tool serves one Rapid.Space project, one compute node (`hox-node`), and a few dozen software releases: mynij-proxy, SBE and aipress24 from `hox.toml`, and the Hop3 catalog apps ported with `hox port` (20 instances on 2026-11-08).

### 1.2 Concepts the tool exposes

- **Software**: a software release we develop, e.g. `mynij-proxy`. It has a repo, a branch, a path to the `software.cfg`, a Software Product in the project, and an instance name.
- **Branch URL**: `https://raw.githubusercontent.com/<repo>/<branch>/<path>/software.cfg`. Registered once; the node rebuilds it in place. Used for development.
- **Pinned URL**: the same with a commit instead of the branch, always as a full SHA (GitHub serves short ones too, but each spelling is another URL for the master and another directory on the node). The commit must be on a branch of origin. Used for versions that must be identifiable on the master.
- **Instance**: the instance tree we request (`mynij-proxy-3`), with a partition, connection parameters and shared frontends. An instance stays on the node it was first requested on, and keeps the URL it started with (2.6): a new node or a new URL means a new name.
- **Test build**: a release built on the node from an unpushed tree in `/srv/hox-dev/`, with no master involved (`hox try-start`).
- **Hop3 recipe**: a catalog app's directory with a `hop3.toml`, the input of `hox port`.

### 1.3 Commands

Every command prints what it does in one line per step and fails with a non-zero exit code and the message of the master or the node when a step fails. Commands that change state are idempotent: running them twice is harmless.

`hox instances [-l] [--json]` (read-only)
- The project's instance trees: status from the node (`up`, `N down`, `not running` as while the node boots, `no services`, `not allocated`, `stopped`, `being destroyed`, `unknown`), memory, frontend URL; `--long` adds their software in hox.toml, branch or commit, and partition. The master's state is what was requested, so an unreachable node makes the status `unknown` and the list still prints. Of the connection parameters, only `url` is read: the others can hold credentials.
- A service counts as down if it ends in `-on-watch` and isn't `RUNNING`, or is in another state than `EXITED` or `STOPPED`. Supervisor restarts nothing and shows no exit code, so a crash reads `EXITED`. A release that leaves a service in `etc/service` although it exits by design names it in `one_shot` of `[software.<name>]`, so that `hox` doesn't count it as down.

`hox status` (read-only)
- The whole system: the node's uptime, partitions used and free, memory and disk, builds not complete, failing promises, services down by name, hox.toml's software with no instance, then the `hox instances` table. It doesn't depend on how many software hox.toml has.

`hox status NAME` (read-only), by software or instance name
- Services: how many run, which are down, memory.
- Instance: requested state, partition, allocation, connection parameters (`url`, `backend-url`, …), and the master's news text for it.
- Shared frontends requested by the instance: allocated or not.
- Software on the node: built (`.completed`), building, failed (last buildout error), for each URL supplied to the node, named after the hox.toml software ("mynij-proxy branch", "abilian-sbe @ 971d944c2").
- Failing promises from the last `slapos node instance` run.
- For the branch URL, whether the local branch head is pushed, and whether raw.githubusercontent.com serves it yet or still an older commit from its cache.

`hox rebuild [SOFTWARE] [--software-only] [--no-wait]`
- The dev loop. Requires the local branch to be pushed (refuses otherwise, telling which commit is missing on the remote).
- Waits until raw.githubusercontent.com serves every file of the software's directory as in the pushed commit (the node fetches `instance.cfg.in` & co. from there too; the CDN caches branch files up to 5 minutes), unless `--no-wait`. Files outside that directory (`stack/`, `component/`) are not checked.
- Runs `slapos node software --force --only-sr <branch URL>` on the node, then `slapos node instance --force --only-cp <partition>` (only the instance's partition: another one's failure, a release still building, isn't ours), streaming the relevant lines (`Installing`/`Uninstalling`, errors, end of run; not buildout's `Updating x.` for every unchanged part). When the node's cron run holds slapgrid's lock (exit 10), retries every 15 s for up to 15 minutes. Exit 2 of the instance run (promises fail) is reported, not fatal.
- Ends with `hox check`; its exit code is the checks' (1 if one fails).

`hox try-start SOFTWARE [--timeout 300]`
- Starts a test build's real processes on the node, without the master: what `/srv/hox-dev/run-build.sh <app>` built from a tree copied there (see `notes/porting-from-hop3/`, "Building a release on the node without the master"). Finds in a minute what a deploy finds after the checks time out (45 minutes).
- `try_start.py` is sent over SSH and run by the node's Python (`/opt/slapos/parts/python3`) as `slapsoft`: stdlib only. It reads the instance profile the build rendered and runs the process scripts of the release, with their environment, behind their haproxy, with a PostgreSQL of their own on the stack's port, in a temporary directory removed at the end.
- Stops at the first failure (supervisord's restarts count as one, or no answer before the timeout) and prints the logs. It has neither frontend, instance parameters nor monitoring, so it doesn't replace a test through the frontend.

`hox build-report [SOFTWARE]` (read-only)
- Writes Markdown for reports, from the node's software logs (rotated ones too, 14 days kept).
- For each build run: start, end, duration and result ("stopped" for a run killed without a result).
- The longest parts, from buildout's "Installing x." lines, each lasting until the next.
- The release directory and shared part sizes, disk used, CPUs and make jobs.

`hox check [SOFTWARE]` (read-only)
- The acceptance checks of `[[software.<name>.checks]]`, run with curl on the node (the backend is only reachable over re6st; TLS not verified). Each check is a URL templated with the instance's connection parameters (`{backend-url}/ping`, `{url}/`), optional request headers, and an expected status and/or response headers; with neither, any HTTP answer passes. A check whose parameter isn't published yet (a frontend still coming) fails with that reason. Exit 1 if one fails.

`hox logs {software,instance,app} [--software NAME] [-f] [-n N]`
- Tails the node's software log, instance log, or the instance's service logs (`/srv/slapgrid/<partition>/.<partition>_*.log`), over SSH.

`hox request [SOFTWARE] [--name NAME] [--pinned COMMIT] [--replace] [--yes]`
- Requests the instance on our node (`computer_guid` filter, software type from the config) with the branch URL, or a pinned URL (`--pinned` checks that the commit is pushed).
- An existing instance gets its original request again (URL, type, filter, parameters read back from the master), which is harmless.
- Parameters: with a `[software.<name>.parameters]` table, every request sends it, replacing the instance's parameters; without one, they stay as they are. The master stores them in clear and the project's members see them in the panel.
- If the instance already exists with another URL, `hox` reports it and stops: the master refuses an in-place URL change with a 500 (see 2.6). `--replace` destroys it first and requests the next free name (`<base>-<n+1>`), after confirmation (`--yes` skips it), then tells to update `instance` in hox.toml (`hox` doesn't write its config file).

`hox destroy NAME [--yes]`
- Destroys an instance tree by re-sending its original request (URL, software type, filter read back from the master) with state `destroyed`. Asks for confirmation unless `--yes`.

`hox stop NAME`, `hox start NAME`
- The same re-sent request with state `stopped` or `started`: the node stops the instance's processes and keeps the data, or starts them again, within a minute or two. Refuses a destroyed instance. No confirmation: both are undone by the other.

`hox supply [SOFTWARE] [--pinned COMMIT] [--remove]`
- Supplies (or removes) the branch URL, or a pinned URL, on our node.

`hox release COMMIT [--software NAME]`
- Makes a pinned version allocatable: supply the pinned URL, add it as a Software Release of the product, tick (release × software type) in the Allocation Supply. Does not move the instance (use `hox request --pinned … --replace`).

`hox setup [SOFTWARE]`
- One-time, idempotent project setup, the panel work of 2026-10-28/29: Software Product with the software type, branch URL as a release, Allocation Supply on our node with the cell ticked and validated, branch URL supplied. Prints what already existed and what it created.
- `hox setup --frontend` (not built yet, needs the Remote Node first): Software Product `apache-frontend` (release `http://git.erp5.org/gitweb/slapos.git/blob_plain/HEAD:/software/apache-frontend/software.cfg`, type `default`) and an Allocation Supply on the project's Remote Node to the CDN. Needs the Remote Node, which Rapid.Space support has to make possible (we're not a member of Canary.CDN, PROJ-318); fails with that explanation if there is none.

`hox deploy [SOFTWARE]`
- Refuses an unpushed branch and waits for GitHub to serve it, as `rebuild` does (before touching the master). Then `setup`, `request`, and waits: for the allocation (30 min at most), for the node's build (the node's cron builds what is supplied; 8 hours at most), forces one instance run of the instance's partition, and waits for the checks to pass (45 min at most: frontends take a while). Each wait says what it waits for. Run it again after a failure: what is done is skipped.

`hox node-certificate`, `hox re6st-token`
- What a new node's registration needs (node-deployer's `SLAPOS_NODE` and `RE6ST_TOKEN`).
- `node-certificate`: the panel's "Add Compute Node" on the project (`Project_addSlapOSComputeNode`), which creates the node and replies (as `application/hal+json`) with a form holding the node's reference, certificate and key, printed as JSON. "Add Compute Node Token" doesn't work: that script reads `configuration_slapos_master_api` from the web site it is called through, which only panel.rapid.space has, and panel.rapid.space doesn't take our client certificate (401); the old `slapos token` API returns 410. The master gives a certificate only once: if the node exists, it runs the node's "Revoke Certificate" then "Request Certificate" (`ComputeNode_revokeCertificate`, `ComputeNode_requestCertificate`), which returns the same form.
- `re6st-token`: a shared instance of the re6st registry (`re6st_release`) in the registry's project (`re6st_project`, "Re6st Registries", via the slap request's `project_reference`), whose `token` connection parameter appears after 10+ minutes. Requested under a fixed name, so asking again gives the same token.

`hox versions [LOCK_FILE] [--python 3.12]` (offline, no `hox.toml`)
- Prints a buildout `[versions]` section from a project's `uv.lock` (default `./uv.lock`), to paste into a release's `versions.cfg`: the root project's run-time dependencies (no dev groups), markers evaluated for Linux x86_64 and the given Python, pinned as uv resolved them.
- A package gets a `:whl` pin when PyPI has a wheel the node can install (pure, or manylinux x86_64 for that Python). Comments list three groups: the packages from binary wheels (C libraries bundled); those buildout must build from their sdist; those it can't install at all.
- Leaves out buildout's tools (pip, setuptools, wheel, zc.buildout), whose versions are SlapOS's, and gives no `:whl` pin to what SlapOS's stack builds itself for slapos.cookbook (cffi, lxml, pyyaml): a wheel pin breaks that build.

`hox port RECIPE SLAPOS [--python 3.13] [--source DIR] [--venv]` (offline but for PyPI and uv, no `hox.toml`)
- A SlapOS software release from a Hop3 recipe, on the hop3 stack (`stack/hop3/` of `slapos_abilian_edge`, branch `hop3-experiment`). Writes `SLAPOS/software/<app id>/`: `software.cfg`, the recipe's files in `hop3/` (without the catalog's overlay: `catalog.toml`, readmes, icon, screenshots, `check.py`), and for a Python app a `versions.cfg` from the recipe's `requirements.txt` (wheels looked up on PyPI).
- One mapper per `hop3.toml` section (`build`, `run`, `admin`, `env`, `probe`, `volumes`, `addons`, `proxy`, `healthcheck`); sections that don't apply on SlapOS are ignored, any other is listed as not mapped, and so is each value a mapper can't translate. The list is printed after the files: read it.
- Toolchains: python, go (the Go component picked from the app's `go.mod`), node, php (php-fpm behind nginx, composer when the build runs it), and generic (a prebuilt binary: provisional, marked as such in the release). Only the local builder. Addons: postgres, and mysql as MariaDB in the partition.
- `--source`: for an app installed from its source (no `requirements.txt`), an unpacked copy of what the recipe fetches, for the app's name and dependencies (resolved with `uv pip compile`), or to recognise a Go app. `--venv`: install a Python app with pip in a dedicated venv (packages with compiled code from their sdists, hashes pinned), where buildout would install eggs, into `software/<app id>-venv/`.
- Overwrites only a release it wrote (one whose `software.cfg` contains "by `hox port`"); refuses any other. The generated release asks for changes to go to the recipe, followed by a new port.
- Every release gets the environment Hop3 gives an app (`HOST_NAME`, `HOP3_PUBLIC_URL`) and `HOP3_BACKEND_URL`, the address the node's health promise uses.

`hox api get RELATIVE_URL [--view VIEW]`, `hox api search QUERY [--select FIELD…] [--limit N]` (read-only, for debugging)
- Raw access to the master's API (what our ad-hoc scripts did), printing JSON.

### 1.4 Non-goals

- Setting up the node itself: node-deployer does it (`create-vm.sh` for the VM, `deploy.py` for SlapOS, `bootstrap.sh` for the whole sequence, which calls `hox re6st-token`, `hox node-certificate` and `hox deploy`).
- Several nodes, several projects, or anything about billing.
- Replacing the `slapos` client for other users; we only cover our loop.
- Committing in the software repos: `hox` reads git state and never writes it. The one exception to "no writes" is `hox port`, which writes a release's files into a checkout's working tree and leaves the commit to us. Bumping the app's pinned `revision` is open (see 4).
- Writing `hox.toml`: when a name changes (`request --replace`), `hox` prints what to change.

### 1.5 Done when

Both met by 2026-10-31:

- From a fresh clone of the workspace, `hox setup` then `hox request` then `hox rebuild` bring a software release from a pushed commit to green checks, with no panel visit. Since 2026-10-30, `hox deploy` does it in one command, and `node-deployer/bootstrap.sh` uses it to redeploy everything on a new node.
- `hox status` covers the questions we used ad-hoc scripts for on 2026-10-28/29 (why is my instance not allocated, did the build fail and why, are frontends allocated).

## 2. Technical spec

### 2.1 Stack

- Python ≥ 3.12, packaged with `uv` (`uv tool install ./hox-cli` gives a `hox` command).
- **cyclopts** for the CLI.
- **httpx2** for HTTP, with the client certificate for TLS client auth.
- **packaging** for `hox versions` and `hox port`: markers, wheel tags, requirement parsing.
- `uv` (the command, not a dependency) for `hox port --source` and `--venv`: `uv pip compile` resolves and hashes pins for Linux x86_64.
- No xml-marshaller after all: we only send four kinds of values (empty dict, dict of strings, string, bool), so `master.marshal` is ten lines, tested against golden values from slapos.core 1.26.1's `slapos.util.dumps` (including its double escaping of `&<>`).
- Standard library otherwise: `tomllib` for the config, `subprocess` for `ssh` and `git`, `xml.etree` where we must read XML.
- `slapos.core` isn't a dependency: everything we need to read is available as JSON from the API (2.4), where slapos.core would give marshalled `slapos.slap` objects.

### 2.2 Layout

```
hox-cli/
  pyproject.toml
  hox.example.toml  # commented copy of the workspace's hox.toml, parsed by a test
  notes/01-spec.md
  src/hox_cli/
    cli.py        # cyclopts app, one function per command, output formatting
    config.py     # load and validate hox.toml into frozen dataclasses
    master.py     # Slap (slap protocol) + Api (hateoas reads, form submits)
    node.py       # ssh/git helpers, slapgrid runs, log parsing
    status.py     # gathering and diagnosing for `hox status` and `hox instances`
    dev.py        # the dev loop: pushed?, GitHub freshness, checks
    panel.py      # `setup`, `release`, `node-certificate`, `re6st-token`: the panel's forms
    deploy.py     # `deploy`: the waits after setup and request
    report.py     # `build-report`: build times and disk use from the node's logs
    try_start.py  # `try-start`: runs on the node, stdlib only
    lock.py       # `versions`: uv.lock -> buildout [versions]
    port.py       # `port`: Hop3 recipe -> SlapOS release
  tests/
    fixtures/     # API replies and node logs recorded on 2026-10-29 (passwords redacted)
```

Split further only if a module grows past a few hundred lines. `cli.py` (700 lines) and `port.py` (500) are past that.

### 2.3 Configuration

`hox.toml`, found in the current directory or its parents (the workspace root holds ours; `hox.example.toml` is a commented copy), or given with `--config`. Parsed once into frozen dataclasses; unknown keys and missing required keys are errors at startup.

```toml
[master]
url = "https://slap.vifib.com/"          # slap protocol; the API URL is discovered via /getHateoasUrl
cert = "~/.slapos/client.crt"
key = "~/.slapos/client.key"
api_tls_name = "slap.vifib.com"          # see 2.4

[project]
reference = "PROJ-20261026.009-F09D5B"

[node]
title = "hox-node"                         # its reference is looked up on the master
ssh = ["ssh", "-F", "node-deployer/ssh_config", "hox-node"]   # the files of -F and -i resolve against hox.toml's directory
re6st_project = "PROJ-112"                 # `hox re6st-token`
re6st_release = "https://lab.nexedi.com/nexedi/slapos/-/raw/1.0.404/software/re6stnet/software.cfg"

[software.mynij-proxy]
repo = "abilian/slapos_abilian_edge"
checkout = "slapos_abilian_edge"           # local clone, for git checks
branch = "mynij-proxy"
path = "software/mynij-proxy/software.cfg"
software_type = "default"
product = "mynij-proxy"                    # its Allocation Supply is "mynij-proxy on hox-node"
instance = "mynij-proxy-4"

[[software.mynij-proxy.checks]]
name = "backend ping"
url = "{backend-url}/ping"                 # {…} = connection parameters
expect_status = 200

[[software.mynij-proxy.checks]]
name = "CORS"
url = "{backend-url}/proxy?url=https://example.org/"
headers = { Origin = "https://example.org" }
expect_header = { access-control-allow-origin = "https://example.org" }

[software.abilian-sbe]
# … the same keys, and:
one_shot = ["sbe_assets", "sbe_create_admin", "sbe_initdb"]   # services of etc/service that exit by design

[software.aipress24.parameters]            # optional: sent with every request, replacing the instance's
ENV_FOR_DYNACONF = "production"
```

Ported apps get a table each too, on the `hop3-experiment` branch: the workspace's `hox.toml` has 21 on 2026-11-09. `hox instances` still lists an instance of the project that no table names, with `-` as the software.

`SOFTWARE` arguments default to the only software when there is one.

### 2.4 Talking to the master

The master has two endpoints, both authenticated with the client certificate. TLS is verified: `api.vifib.com` serves the certificate of `slap.vifib.com` (checked 2026-10-29), so API requests verify it under that name (`master.api_tls_name`, httpx2's `sni_hostname` extension). slapos.core disables verification.

- **slap protocol** on `master.url`: `POST supplySupply` (`url`, `computer_id`, `state`) and `POST requestComputerPartition` (`software_release`, `partition_reference`, `software_type`, and `partition_parameter_xml`, `filter_xml`, `state`, `shared_xml` marshalled). A 408 reply means "requested, not ready yet" and is not an error. The reply body is not decoded. An instance tree's original request is readable from the API: `url_string`, `source_reference` (type), `sla_xml` (filter), `text_content` (parameters), `root_slave` (shared), `slap_state`.
- **API** (hateoas, `ERP5Document_getHateoas` on the URL that `GET /getHateoasUrl` returns, `api.vifib.com` today):
  - search: the root document's `raw_search` template with `query`, `select_list`, `limit` (what `slapos service list` uses);
  - read: `traverse` template with `relative_url` and an optional `view`;
  - forms: a document's `_links` give its actions (`action_object_jio_fast_input` → "Add Software Product", "Add Software Release", "Add Software Type", "Add Allocation Supply", "Add Remote Node"; `action_workflow` → validate, invalidate). Reading an action's view returns the form as JSON: fields with their `key`s and defaults, and `_actions.put` with the URL (`Base_callDialogMethod` or `Base_edit`) and the method name. Submitting = POST of the form fields (`form_id`, `dialog_id`, `dialog_method`, `field_your_…`).
  - an action's form is read from the action link's `href`: it includes `extra_param_json`, without which `form_id` comes back empty. Submitting = POST to `_actions.put.href` of every field's default plus `dialog_method` (in `_actions.put.action` for dialogs, `_actions.action` for scripts) and our values. Certificate auth is enough (tested 2026-10-29: add and delete a software type). A success returns 200/201 with `portal_status_level: success`; a form error 400 with the form re-rendered.
  - `Base_edit` skips fields absent from the POST (their validator raises `KeyError`, which `validate_all` ignores), so an edit sends only what it changes (tested on a product's description).
  - the Allocation Supply matrix is the `matrixbox` field of the supply line's `view`: one `field_matrixbox_allocable_cell_<r>_<t>_0` checkbox per (release, type), with hidden `…variation_category_list_cell_…` and `…predicate_category_list_cell_…` fields to send back unchanged. The matrix is validated as a whole: every field of every cell must be in the POST (checkboxes as `1`/`0`, lists newline-joined), or `Base_edit` skips the matrix.
  - "Add Allocation Supply" on the project (`Project_addSlapOSAllocationSupply`) creates an invalidated supply titled "All compute nodes <date>" on all the project's validated nodes with allocation scope open, with one line per product of the project. Invalidating a supply (`slapos_panel_workflow_fast_invalidate`) adds the lines of products it lacks. Lines are edited on a validated supply without invalidating it.
  - what a form creates shows up in the catalog a few seconds later (ERP5 indexes in the background): `setup` polls for it.
  - deleting a release that a supply cell refers to fails with a 400 and no message: such a release stays in the product, unticked.

`Api` finds forms and fields by their action name and field key, never by position or title, and fails with the list of what it found when one is missing (the panel's forms are not a public API and may change). After each change it reads the result back and checks it (e.g. the release URL is in the product, the cell is ticked, the supply is validated).

### 2.5 Talking to the node

`subprocess.run(config.node.ssh + [command])`. Commands are fixed strings built from config values with `shlex.quote`. Long commands (`slapos node software --force`) stream their output through a filter (`node.shown`) that keeps buildout's `Installing`/`Uninstalling` lines, errors and the end of the run, and drops `Updating` lines.

`try-start` is the one command that sends code: `try_start.py`'s source on stdin to the node's Python, run as `slapsoft`.

Facts read from the node: software dirs (`/opt/slapgrid/<md5(url)>`, `.completed`), logs (`/opt/slapos/log/slapos-node-{software,instance}.log`), partitions (`/srv/slapgrid/slappartN`). The md5 is computed locally.

### 2.6 Known master behaviours the tool must handle

- Allocation needs the release in a validated Allocation Supply of the project; without it the instance stays at status `none` with no error anywhere. `hox status` reports it when it sees an instance with no partition and no matching supply cell.
- Changing an existing instance's URL with `requestComputerPartition` gets a 500, as does any request whose parameters differ from the original (filter, software type). Destroying works only by re-sending the original parameters with `state = destroyed`.
- The master's changes are slow to propagate: allocation takes minutes, frontends longer. Commands that wait poll with a timeout and say what they're waiting for.

### 2.7 Errors and output

- One exception type per boundary (`MasterError`, `NodeError`, `DevError`, `ConfigError`, and for the offline commands `LockError`, `PortError`), with the remote message. `cli.py` turns them into a one-line message and exit code 1; `--debug` shows the traceback in place of that line. Showing the HTTP exchange too, as first planned, isn't built: the master's errors include the HTTP status and the first 200 characters of the body.
- Plain text output; `--json` on `instances`, `status` and the `api` commands for scripts and agents.

### 2.8 Tests

- `uv run pytest` (184 tests, under 2 s), in three tiers: `tests/a_unit/` (159), `tests/b_integration/` (22: real git repositories, and `cli` commands through several modules), `tests/c_e2e/` (3: the `hox` command as a process, offline). Unit tests with `httpx2.MockTransport`: request payload encoding (compared with what slapos.core 1.26.1 sends), form discovery and submission against recorded API JSON (the dumps from 2026-10-29, saved as fixtures), check evaluation.
- The node is faked: `Node` takes `run`, `popen` and `sleep` as arguments, parsers are tested on recorded logs (`fixtures/software.log`, `instance.log`), and command tests replace `cli`'s helpers (`_config`, `_api`, `_node`, `Slap.connect`) with `monkeypatch`.
- `hox port` tests use small recipes written in the test, with PyPI, `uv` and the source tree passed in as functions or directories.
- No test against the live master or node. A manual smoke test: `hox status` and `hox api search` on the real project.

## 3. Build history

1. 2026-10-29: config, `api get/search`, `status`.
2. 2026-10-29: `rebuild`, `check`, `logs` (SSH; the daily loop).
3. 2026-10-29: `supply`, `request`, `destroy` (slap protocol).
4. 2026-10-29: the form submission spike (add a software type to the product and remove it again) confirmed that certificate auth works for form POSTs.
5. 2026-10-29: `setup` and `release`, live-tested for the parts that existed already and for a new release (added, cell ticked on the validated supply, supplied).
6. 2026-10-30: `deploy`, `node-certificate`, `re6st-token`, and the node found by its title (`[node] title`; the reference changes with each registration), for a rebuild from scratch on a new VM. Run live by `node-deployer/bootstrap.sh` on `hox-node`. Creating a product and a supply and validating it then ran live too, for each new software.
7. 2026-10-31: `build-report`, `versions`; instance runs limited to the instance's partition.
8. 2026-11-01: instance parameters from `hox.toml`.
9. 2026-11-03 → 08: `port` (Python, then Go, Node, PHP, venv, MariaDB, composer), `try-start`, `instances` and the whole-system `status`, `stop` and `start`.

Not built: `setup --frontend`. Our project's instances get their frontends from Rapid.Space's shared frontend since the one-time panel work of the runbook's phase 4c, so it would only serve another project.

## 4. Open questions

- **Moving an instance to a new URL** without destroying it: the master has Upgrade Decisions (API `jump_to_slapos_upgrade_decision`), created when a product gets a newer release. Check whether accepting one is how a URL change is meant to happen, and add `hox upgrade` if so.
- **The app's pinned revision** (`revision =` in the release's gitclone part): a change in the app repo needs a commit in the release repo too. Either `hox` learns to bump it (it then writes to a repo, which 1.4 excludes), or the release drops the pin and the gitclone part is forced to update on rebuild.
- **Local path URLs**: whether the master accepts a release URL that is a path on the node (rsync in place of a push, with no raw.githubusercontent.com). Would make `rebuild` independent of GitHub. In the meantime, `/srv/hox-dev/` and `try-start` test an unpushed tree without the master, but not as a deployed instance.
- **A branch that moved past what the node built**: `hox deploy` should warn when the node's build of the branch URL is older than the pushed head (from `notes/porting-from-hop3/`, "Next").
- **Splitting `cli.py`**: past the few hundred lines of 2.2, mostly output formatting (`format_*`), which could move next to what it formats.
