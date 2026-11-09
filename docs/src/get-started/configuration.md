# Configuration

`hox` reads a `hox.toml`: the first one in the current directory or its parents, or the one given with `--config`. Keep it at the root of a workspace that also holds the checkouts of your software repositories: relative paths in it resolve against its own directory.

The repository has a commented example, `hox.example.toml`. Start from a copy of it.

## A minimal file

```toml
[master]
url = "https://slap.vifib.com/"
cert = "~/.slapos/client.crt"
key = "~/.slapos/client.key"
api_tls_name = "slap.vifib.com"

[project]
reference = "PROJ-20260101.001-0A1B2C"

[node]
title = "my-node"
ssh = ["ssh", "-F", "ssh_config", "my-node"]
re6st_project = "PROJ-112"
re6st_release = "https://lab.nexedi.com/nexedi/slapos/-/raw/1.0.404/software/re6stnet/software.cfg"

[software.my-app]
repo = "my-org/slapos"
checkout = "slapos"
branch = "my-app"
path = "software/my-app/software.cfg"
software_type = "default"
product = "my-app"
instance = "my-app-1"

[[software.my-app.checks]]
name = "backend"
url = "{backend-url}/"
expect_status = 200
```

- `[master]`: the SlapOS master and your client certificate. `api_tls_name` is needed on Rapid.Space: see [the reference](../reference/config.md#master).
- `[project]`: the reference of your project, as the panel shows it.
- `[node]`: your compute node's title on the master, and how to reach it over SSH. `hox` looks the node's reference up by its title, so a node registered again needs no change here.
- `[software.<name>]`: one table per software release you develop. Most commands take its name as their `SOFTWARE` argument, which is optional when there is only one.
- `[[software.<name>.checks]]`: what "the app works" means. `hox check`, `hox rebuild` and `hox deploy` run them. `{backend-url}` is one of the instance's connection parameters: use the names your release publishes.
- `re6st_project` and `re6st_release` serve only `hox re6st-token`, when you [set up a node](../guides/new-node.md). The values above are Rapid.Space's.

Unknown keys and missing required keys are errors, reported when any command starts. The [`hox.toml` reference](../reference/config.md) lists every key.

## Checking it

```
hox status
```

This reads from the master and the node, and changes nothing. If it prints your node's uptime and its partitions, the certificate and SSH work.

Next: [First deploy](first-deploy.md).
