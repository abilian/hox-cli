# Releases and instances

## Branch URL and pinned URL

A software in `hox.toml` has two kinds of release URL:

- the **branch URL**, `https://raw.githubusercontent.com/<repo>/<branch>/<path>`, for development: the node rebuilds it in place;
- a **pinned URL**, the same with a commit instead of the branch, for a version that has to be identifiable on the master.

A pinned URL always uses the full 40-character SHA. GitHub serves short ones too, but each spelling is another URL for the master and another directory on the node. The commit must be pushed.

## Making a pinned version allocatable

```
hox release 0123456789abcdef0123456789abcdef01234567 --software my-app
```

This does `hox setup`'s steps for the pinned URL: it adds the URL as a release of the product, ticks it in the Allocation Supply, and supplies it on the node. The instance doesn't move.

## Moving an instance to another URL

The SlapOS master refuses to change an existing instance's release URL: it answers HTTP 500 to any request whose URL, software type or filter differ from the instance's original request. So moving to another URL means a new instance:

```
hox request my-app --pinned 0123456… --replace
```

`--replace` asks for confirmation (`--yes` skips it), destroys the current instance, and requests the next free name (`my-app-1` becomes `my-app-2`). `hox` doesn't write its configuration: it tells you to set `instance` to the new name in `hox.toml`.

Without `--replace`, `hox request` on an instance that runs another URL stops and says so. On an instance that runs the same URL, it resends the original request, which is harmless.

## Instance parameters

With a `[software.<name>.parameters]` table in `hox.toml`, every `hox request` (and so `hox deploy`) sends those parameters, replacing the ones the instance has. Without the table, the instance keeps its parameters.

The master stores parameters in clear. Every member of the project can read them in the panel.

## Stopping, starting, destroying

```
hox stop my-app-1       # stop the processes, keep the data
hox start my-app-1      # start them again
hox destroy my-app-1    # asks for confirmation; --yes skips it
```

All three resend the instance's original request, read back from the master, with another state: the master accepts nothing else. The node acts within a minute or two; `hox instances` shows the result.

## Supplying by hand

```
hox supply my-app                     # the branch URL
hox supply my-app --pinned 0123456…   # a pinned URL
hox supply my-app --remove            # remove it from the node
```

`hox setup`, `hox release` and `hox deploy` already supply what they register.
