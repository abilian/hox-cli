# hox.toml

`hox` reads the first `hox.toml` in the current directory or its parents, or the file given with `--config`. It is parsed when a command starts: an unknown section or key, a missing required key, or a file with no `[software.<name>]` table is an error.

Paths (`cert`, `key`, `checkout`, and the files of `ssh`'s `-F` and `-i` options) resolve against the directory of `hox.toml`; `~` is expanded.

## `[master]`

| Key | Required | Description |
|---|---|---|
| `url` | yes | The master's slap protocol URL, e.g. `https://slap.vifib.com/`. The API's URL is discovered from it (`getHateoasUrl`). |
| `cert` | yes | Your client certificate, from `slapos configure client`. |
| `key` | yes | Its key. |
| `api_tls_name` | no | The name to verify the API's TLS certificate against. On Rapid.Space, `api.vifib.com` serves the certificate of `slap.vifib.com`: set `slap.vifib.com`, so that `hox` can verify it rather than turn verification off. |

## `[project]`

| Key | Required | Description |
|---|---|---|
| `reference` | yes | The project's reference, e.g. `PROJ-20260101.001-0A1B2C`. |

## `[node]`

| Key | Required | Description |
|---|---|---|
| `title` | yes | The compute node's title on the master. `hox` looks its reference up by title, so a new registration needs no change. |
| `ssh` | yes | The command that runs a command on the node as root, as a list; `hox` appends the command. E.g. `["ssh", "-F", "ssh_config", "-o", "LogLevel=ERROR", "my-node"]`. |
| `re6st_project` | yes | The project of the re6st registry, for `hox re6st-token`. On Rapid.Space: `PROJ-112` ("Re6st Registries"). |
| `re6st_release` | yes | The re6st registry's release URL, for `hox re6st-token`. |

## `[software.<name>]`

Each table describes a software release you develop. `<name>` is what commands take as `SOFTWARE`.

| Key | Required | Description |
|---|---|---|
| `repo` | yes | The GitHub repository the node fetches the release from, as `owner/name`. |
| `checkout` | yes | A local clone of it, for the git checks (pushed, served). |
| `branch` | yes | The branch; it gives the branch URL. |
| `path` | yes | The path of the release's `software.cfg` in the repository. |
| `software_type` | yes | The software type to request, usually `default`. |
| `product` | yes | The Software Product's title on the master. Its Allocation Supply is titled `<product> on <node title>`. |
| `instance` | yes | The instance's name. An instance stays on the node it was first requested on, and keeps its URL: a new node, or `hox request --replace`, means a new name. |
| `one_shot` | no | Services in the partition's `etc/service` that exit once their work is done: their exit isn't counted as a failure. Default: none. |

The release URLs are derived from these keys: `https://raw.githubusercontent.com/<repo>/<branch>/<path>` for the branch, with a full commit SHA in place of the branch for a pinned URL.

### `[software.<name>.parameters]`

Optional. The instance's parameters, as strings, sent with every `hox request` and `hox deploy`. They replace those the instance has; without this table, the instance keeps its parameters. The master stores them in clear. Every member of the project sees them in the panel.

```toml
[software.my-app.parameters]
ENV_FOR_DYNACONF = "production"
```

### `[[software.<name>.checks]]` { #checks }

The acceptance checks, run with curl on the node by `hox check`, `hox rebuild` and `hox deploy`. TLS isn't verified.

| Key | Required | Description |
|---|---|---|
| `name` | yes | Shown in the results. |
| `url` | yes | The URL to request. `{…}` takes the instance's connection parameters: `{backend-url}`, `{url}` (the frontend). A check whose parameter isn't published yet fails with that reason. |
| `headers` | no | Request headers, as a table. |
| `expect_status` | no | The expected HTTP status. |
| `expect_header` | no | Expected response headers, as a table. |

With neither `expect_status` nor `expect_header`, any HTTP answer passes.

```toml
[[software.my-app.checks]]
name = "CORS"
url = "{backend-url}/proxy?url=https://example.org/"
headers = { Origin = "https://example.org" }
expect_header = { access-control-allow-origin = "https://example.org" }
```
