# hox-cli: specification

`hox` drives our SlapOS work on Rapid.Space from the terminal: what the `slapos` client does, what we did by hand in the panel (panel.rapid.space), and what we ran over SSH on our compute node. The goal is that building, deploying and checking a software release never needs the web panel.

Context: `../../notes/runbooks/own-slapos-node-plan.md` (how the node was set up and what we learned, 2026-10-26 → 29) and `../../node-deployer/` (pyinfra deploy of the node itself, out of scope here).

## 1. Functional spec

### 1.1 Users and setting

One developer (or a coding agent) on a laptop, with:

- a SlapOS client certificate for their account (`~/.slapos/client.crt`, `client.key`, from `slapos configure client`);
- root SSH to the compute node (through `vigie-a`, as in `node-deployer/ssh_config`);
- push access to the GitHub repos the software releases come from.

One Rapid.Space project, one compute node, a few software releases (mynij-proxy now, SBE next).

### 1.2 Concepts the tool exposes

- **Software**: a software release we develop, e.g. `mynij-proxy`. It has a repo, a branch, a path to its `software.cfg`, a Software Product in the project, and an instance name.
- **Branch URL**: `https://raw.githubusercontent.com/<repo>/<branch>/<path>/software.cfg`. Registered once; the node rebuilds it in place. Used for development.
- **Pinned URL**: the same with a commit instead of the branch. Used for versions that must be identifiable on the master.
- **Instance**: the instance tree we request (`mynij-proxy-3`), its partition, its connection parameters, its shared frontends.

### 1.3 Commands

Every command prints what it does in one line per step and fails with a non-zero exit code and the master's or node's own message when a step fails. Commands that change state are idempotent: running them twice is harmless.

`hox status [SOFTWARE]` (read-only)
- Instance: requested state, partition, allocation, connection parameters (`url`, `backend-url`, …), and the master's news text for it.
- Shared frontends requested by the instance: allocated or not.
- Software on the node: built (`.completed`), building, failed (last buildout error), for each supplied URL.
- Failing promises from the last `slapos node instance` run.
- For the branch URL: the commit raw.githubusercontent.com currently serves vs. the local branch head.

`hox rebuild [SOFTWARE] [--software-only] [--no-wait]`
- The dev loop. Requires the local branch to be pushed (refuses otherwise, telling which commit is missing on the remote).
- Waits until raw.githubusercontent.com serves the pushed `software.cfg` (its cache keeps branch files up to 5 minutes), unless `--no-wait`.
- Runs `slapos node software --force --only-sr <branch URL>` on the node, then `slapos node instance --force`, streaming the relevant lines (`Installing`, `Updating`, errors).
- Ends with `hox check`.

`hox check [SOFTWARE]` (read-only)
- The acceptance checks, from the node (the backend is only reachable over re6st): backend `/ping` (TLS not verified), frontend `/ping` when `url` is published, and the CORS header on `/proxy` with an `Origin:` header. Checks are declared per software in the config (method, path, headers, expected status/header), so SBE can declare its own.

`hox logs [SOFTWARE] {software,instance,app} [-f] [-n N]`
- Tails the node's software log, instance log, or the instance's service logs (`/srv/slapgrid/<partition>/.<partition>_*.log`), over SSH.

`hox request [SOFTWARE] [--name NAME] [--pinned COMMIT]`
- Requests the instance on our node (`computer_guid` filter, software type from the config) with the branch URL, or a pinned URL.
- If the instance already exists with another URL, says so and stops: the master answers 500 to an in-place URL change (see 2.6). `--replace` destroys it first and requests a new name (`<base>-<n+1>`), after confirmation.

`hox destroy NAME [--yes]`
- Destroys an instance tree by re-sending its original request (URL, software type, filter read back from the master) with state `destroyed`. Asks for confirmation unless `--yes`.

`hox supply [SOFTWARE] [--pinned COMMIT] [--remove URL]`
- Supplies (or removes) a software release on our node.

`hox release [SOFTWARE] COMMIT`
- Makes a pinned version allocatable: supply the pinned URL, add it as a Software Release of the product, tick (release × software type) in the Allocation Supply. Does not move the instance (use `hox request --pinned … --replace`).

`hox setup [SOFTWARE]`
- One-time, idempotent project setup, the panel work of 2026-10-28/29: Software Product with the software type, branch URL as a release, Allocation Supply on our node with the cell ticked and validated, branch URL supplied. Prints what already existed and what it created.
- `hox setup --frontend`: Software Product `apache-frontend` (release `http://git.erp5.org/gitweb/slapos.git/blob_plain/HEAD:/software/apache-frontend/software.cfg`, type `default`) and an Allocation Supply on the project's Remote Node to the CDN. Needs the Remote Node, which Rapid.Space support has to make possible (we're not a member of Canary.CDN, PROJ-318); fails with that explanation if there is none.

`hox api get RELATIVE_URL [--view VIEW]`, `hox api search QUERY [--select FIELD…]` (read-only, for debugging)
- Raw access to the master's API (what our ad-hoc scripts did), printing JSON.

### 1.4 Non-goals

- Setting up the node itself (node-deployer does it).
- Several nodes, several projects, or anything about billing.
- Replacing the `slapos` client for other users; we only cover our loop.
- Editing or committing in the software repos (`hox` reads git state, never writes it). Bumping the app's pinned `revision` is open (see 4).

### 1.5 Done when

- From a fresh clone of the workspace, `hox setup` then `hox request` then `hox rebuild` bring a software release from a pushed commit to green checks, with no panel visit.
- `hox status` answers the questions we answered with ad-hoc scripts on 2026-10-28/29 (why is my instance not allocated, did the build fail and why, are frontends allocated).

## 2. Technical spec

### 2.1 Stack

- Python ≥ 3.12, packaged with `uv` (`uv tool install ./hox-cli` gives a `hox` command).
- **cyclopts** for the CLI.
- **httpx2** for HTTP, with the client certificate for TLS client auth.
- **xml-marshaller** to encode the slap protocol's form fields (it is what slapos.core uses, so the master gets byte-identical payloads). Added with the slap protocol commands (step 3).
- Standard library otherwise: `tomllib` for the config, `subprocess` for `ssh` and `git`, `xml.etree` where we must read XML.
- No dependency on `slapos.core`: its replies are marshalled `slapos.slap` objects, but everything we need to read is available as JSON from the API (2.4).

### 2.2 Layout

```
hox-cli/
  pyproject.toml
  notes/01-spec.md
  src/hox_cli/
    __init__.py
    cli.py        # cyclopts app, one function per command, output
    config.py     # load and validate hox.toml into frozen dataclasses
    master.py     # SlapClient (slap protocol) + Api (hateoas reads, form submits)
    node.py       # ssh/git helpers, log parsing
    status.py     # gathering and diagnosing for `hox status`
  tests/
    fixtures/     # API replies and node logs recorded on 2026-10-29 (passwords redacted)
```

Split further only if a module grows past a few hundred lines.

### 2.3 Configuration

`hox.toml`, found in the current directory or its parents (the workspace root holds ours), or given with `--config`. Parsed once into frozen dataclasses; unknown keys and missing required keys are errors at startup.

```toml
[master]
url = "https://slap.vifib.com/"          # slap protocol; the API URL is discovered via /getHateoasUrl
cert = "~/.slapos/client.crt"
key = "~/.slapos/client.key"
api_tls_name = "slap.vifib.com"          # see 2.4

[project]
reference = "PROJ-20261026.009-F09D5B"

[node]
reference = "COMP-20261027.021-9D1365"
ssh = ["ssh", "-F", "node-deployer/ssh_config", "hox-node"]   # relative paths resolve against hox.toml's directory

[software.mynij-proxy]
repo = "abilian/slapos_abilian_edge"
checkout = "slapos_abilian_edge"           # local clone, for git checks
branch = "mynij-proxy"
path = "software/mynij-proxy/software.cfg"
software_type = "default"
product = "mynij-proxy"
supply_title = "mynij-proxy on abilian-hox-node-1"
instance = "mynij-proxy-3"

[[software.mynij-proxy.checks]]
name = "backend ping"
url = "{backend-url}/ping"                 # {…} = connection parameters
expect_status = 200

[[software.mynij-proxy.checks]]
name = "CORS"
url = "{backend-url}/proxy?url=https://example.org/"
headers = { Origin = "https://example.org" }
expect_header = { access-control-allow-origin = "https://example.org" }
```

`SOFTWARE` arguments default to the only software when there is one.

### 2.4 Talking to the master

Two endpoints, both authenticated with the client certificate. TLS is verified: `api.vifib.com` serves the certificate of `slap.vifib.com` (checked 2026-10-29), so API requests verify it under that name (`master.api_tls_name`, httpx2's `sni_hostname` extension) instead of disabling verification as slapos.core does.

- **slap protocol** on `master.url`: `POST supplySupply` (`url`, `computer_id`, `state`) and `POST requestComputerPartition` (`software_release`, `partition_reference`, `software_type`, and `partition_parameter_xml`, `filter_xml`, `state`, `shared_xml` marshalled with xml-marshaller). A 408 answer means "requested, not ready yet" and is not an error. The reply body is not decoded.
- **API** (hateoas, `ERP5Document_getHateoas` on the URL that `GET /getHateoasUrl` returns, `api.vifib.com` today):
  - search: the root document's `raw_search` template with `query`, `select_list`, `limit` (what `slapos service list` uses);
  - read: `traverse` template with `relative_url` and an optional `view`;
  - forms: a document's `_links` give its actions (`action_object_jio_fast_input` → "Add Software Product", "Add Software Release", "Add Software Type", "Add Allocation Supply", "Add Remote Node"; `action_workflow` → validate, invalidate). Reading an action's view returns the form as JSON: fields with their `key`s and defaults, and `_actions.put` with the URL (`Base_callDialogMethod` or `Base_edit`) and the method name. Submitting = POST of the form fields (`form_id`, `dialog_id`, `dialog_method`, `field_your_…`).
  - the Allocation Supply matrix is the `matrixbox` field of the supply line's `view`: one `field_matrixbox_allocable_cell_<r>_<t>_0` checkbox per (release, type), with hidden `…variation_category_list_cell_…` and `…predicate_category_list_cell_…` fields to send back unchanged.

`Api` finds forms and fields by their action name and field key, never by position or title, and fails with the list of what it found when one is missing (the panel's forms are not a public API and may change). After each change it reads the result back and checks it (e.g. the release URL is in the product, the cell is ticked, the supply is validated).

### 2.5 Talking to the node

`subprocess.run(config.node.ssh + [command])`. Commands are fixed strings built from config values with `shlex.quote`. Long commands (`slapos node software --force`) stream their output through a filter that keeps buildout's `Installing`/`Updating`/`Uninstalling` lines, errors and the final status.

Facts read from the node: software dirs (`/opt/slapgrid/<md5(url)>`, `.completed`), logs (`/opt/slapos/log/slapos-node-{software,instance}.log`), partitions (`/srv/slapgrid/slappartN`). The md5 is computed locally.

### 2.6 Known master behaviours the tool must handle

- Allocation needs the release in a validated Allocation Supply of the project; without it the instance stays at status `none` with no error anywhere. `hox status` says so when it sees an instance with no partition and no matching supply cell.
- Changing an existing instance's URL with `requestComputerPartition` gets a 500, as does any request whose parameters differ from the original (filter, software type). Destroying works only by re-sending the original parameters with `state = destroyed`.
- Master answers are slow to propagate: allocation takes minutes, frontends longer. Commands that wait poll with a timeout and say what they're waiting for.

### 2.7 Errors and output

- One exception type per boundary (`MasterError`, `ApiFormError`, `NodeError`, `ConfigError`), carrying the remote message. `cli.py` turns them into a one-line message and exit code 1; `--debug` shows the traceback and the HTTP exchange.
- Plain text output; `--json` on `status` and the `api` commands for scripts and agents.

### 2.8 Tests

- Unit tests with `httpx2.MockTransport`: request payload encoding (compared with what slapos.core 1.26.1 sends), form discovery and submission against recorded API JSON (the dumps from 2026-10-29 in the session, saved as fixtures), check evaluation.
- No test against the live master in CI. A manual smoke test: `hox status` and `hox api search` on the real project.

## 3. Build order

1. Config, `api get/search`, `status` (read-only; replaces our ad-hoc scripts).
2. `rebuild`, `check`, `logs` (SSH; the daily loop).
3. `supply`, `request`, `destroy` (slap protocol).
4. Form submission spike: one harmless write (add a software type to the product and remove it again) to confirm that certificate auth works for form POSTs. If it doesn't, find out how the panel authenticates (cookie from a login) before going further.
5. `setup`, `release`, `setup --frontend`.

## 4. Open questions

- **Moving an instance to a new URL** without destroying it: the master has Upgrade Decisions (API `jump_to_slapos_upgrade_decision`), created when a product gets a newer release. Check whether accepting one is how a URL change is meant to happen, and add `hox upgrade` if so.
- **The app's pinned revision** (`revision =` in the release's gitclone part): a change in the app repo needs a commit in the release repo too. Either `hox` learns to bump it (it then writes to a repo, which 1.4 excludes), or the release drops the pin and the gitclone part is forced to update on rebuild.
- **Local path URLs**: whether the master accepts a release URL that is a path on the node (rsync instead of push, no raw.githubusercontent.com). Would make `rebuild` independent of GitHub.
