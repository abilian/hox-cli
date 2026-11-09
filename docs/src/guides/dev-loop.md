# The development loop

You work on a software release in a branch. The master knows one URL for it, the **branch URL**:

```
https://raw.githubusercontent.com/<repo>/<branch>/<path>
```

It is registered once (`hox setup` or `hox deploy`). After each change, the node rebuilds that same URL in place, so you register nothing new on the master.

## Commit, push, rebuild

```
git commit -am "…" && git push
hox rebuild my-app
```

`hox rebuild`:

1. Refuses if the local branch isn't pushed, and says which commit is missing on the remote.
2. Waits until `raw.githubusercontent.com` serves every file of the software's directory as in the pushed commit, since that server caches branch files for up to 5 minutes. Files outside that directory (`stack/`, `component/`) aren't checked. `--no-wait` skips this.
3. Runs `slapos node software --force --only-sr <branch URL>` on the node, showing what buildout installs and uninstalls, the errors and the end of the run.
4. Runs `slapos node instance --force` on the instance's partition only: a failure in another partition, such as a release still building, isn't yours.
5. Runs the checks, and exits with code 1 if one fails.

`--software-only` stops after the build, with no instance run and no checks.

If the node's own cron run of slapgrid (the SlapOS agent) holds its lock, `hox` retries every 15 seconds for up to 15 minutes. When promises (the instance's health checks) fail at the end of the instance run, `hox` reports them and goes on.

## Checks

```
hox check my-app
```

Runs the `[[software.<name>.checks]]` of `hox.toml` with curl on the node, since the backend is usually reachable only over re6st, the network between SlapOS nodes. Each check requests a URL built from the instance's connection parameters and compares the status and headers of the answer with what you expect. See [the reference](../reference/config.md#checks).

## Logs

```
hox logs software            # slapgrid's software log (builds)
hox logs instance            # slapgrid's instance log (deployments, promises)
hox logs app --software my-app -f
```

With `app`, `hox logs` tails the instance's service logs. Use `-f` to keep following and `-n N` to set the number of lines (50 by default).

## Before you push: test builds

Each rebuild goes through GitHub and the master. For a release you are still shaping, a faster loop builds an unpushed tree on the node directly and starts its processes without the master: see [Porting Hop3 apps](porting-hop3.md#testing-a-build-without-the-master).
