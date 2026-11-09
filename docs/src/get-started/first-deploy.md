# First deploy

With a [configured](configuration.md) `hox.toml` and a software release pushed to its branch, one command takes you from nothing on the master to an instance that passes its checks:

```
hox deploy my-app
```

## What it does

1. **Refuses an unpushed branch.** The node fetches the release from GitHub: a commit that is only local would give it a 404, or an older release. `hox` then waits until `raw.githubusercontent.com` serves the pushed files, since it caches branch files for up to 5 minutes.
2. **Does the panel work** (`hox setup`): it creates the records the master needs before it lets your release run on your node (see the [glossary](../reference/glossary.md)). It opens your node's allocation scope and creates the Software Product with its software type and the branch URL as a release. It also creates the Allocation Supply `<product> on <node title>`, with the (release × software type) cell ticked, and validates it. It skips each step that is already done.
3. **Supplies the branch URL** on your node, so that the node builds it.
4. **Requests the instance** (`hox request`) on your node.
5. **Waits for the allocation** (30 minutes at most): the master allocates a partition on your node to the instance.
6. **Waits for the build** (8 hours at most). The node's own cron builds what is supplied. A large release on a node with an empty cache takes hours: Node.js and LLVM alone took 8 hours for Abilian SBE, a large Python web application.
7. **Runs the instance** once, for the instance's own partition only.
8. **Waits for the checks to pass** (45 minutes at most). Rapid.Space's shared frontend takes a while to publish the instance's public address.

Each wait says what it waits for. If a step fails or times out, fix the cause and run `hox deploy` again: what is done is skipped.

## Then

```
hox status my-app      # the instance, its services, frontends, builds and promises
hox instances          # every instance of the project, with its status
```

The instance's name is `instance` in `hox.toml`. The SlapOS master doesn't let an instance change its release URL, so that name changes when you move to a release at a given commit: see [Releases and instances](../guides/releases.md).

For the daily work after this, see [the development loop](../guides/dev-loop.md).
