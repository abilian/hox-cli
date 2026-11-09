# Runbook: a SlapOS node from scratch

This directory turns an empty machine into a SlapOS compute node on Rapid.Space, then deploys every application of a `hox.toml` on it, with no visit to the web panel. It is how we rebuild the node that runs the [21 applications](../apps/) when we lose it: one command, about a day of builds, and each step can be run again after a failure.

The node runs Debian 12. In our case it is a VM on a libvirt host of ours, which `create-vm.sh` creates. Any Debian 12 machine with root SSH works too: skip that step.

## What it does

| Step | Tool | Time |
|---|---|---|
| 1. Create the VM: Debian 12 cloud image, fixed address, root SSH with your keys | `create-vm.sh` | a few minutes |
| 2. Get the node's re6st token from the master | `hox re6st-token` | 10 minutes or more, the first time |
| 3. Create the compute node on the master and get its certificate | `hox node-certificate` | seconds |
| 4. Install re6st and SlapOS, register on re6st, configure the node with the certificate, format the partitions | `pyinfra … deploy.py` | minutes |
| 5. For each software: product, supply, request, build, checks | `hox deploy` | hours for the first one, then minutes to half an hour each |

`bootstrap.sh` runs them in that order. Each one skips what is already done.

`deploy.py` is a port of the two installers Nexedi publishes on `deploy.rapid.space`: `gnet/re6st` and `slapos` (playbooks `gnet-re6stnet.yml` and `slapos.yml` of [nexedi/slapos.package](https://lab.nexedi.com/nexedi/slapos.package)), without their prompts. It also sets three things the installers leave out:

- `MAKEFLAGS=-j<CPUs>` for the builds, which otherwise use one CPU (a cold build that includes LLVM then takes over 8 hours);
- shared parts (`/opt/slapgrid/shared`), so the components (compilers, Python, PostgreSQL…) are built once per node, not once per software release;
- the partition count of `node.env`.

The installers register a node with a token made in the panel, but on Rapid.Space that token works only through `panel.rapid.space`, which doesn't accept a client certificate. `hox node-certificate` creates the node and gets its certificate instead, and `register_with_certificate.py` runs the code of `slapos node register` with it.

## Before you start

You need, on the master (once per account):

- A Rapid.Space account, and a project for your nodes and instances. Its reference (`PROJ-…`) goes in `hox.toml`.
- A client certificate: `slapos configure client`, with a credential token from the panel (My Account). `hox` reads it from `~/.slapos/`.
- Access to Rapid.Space's shared frontend for your project. Without it, instances never get their public URL (`url` stays empty). Ask Rapid.Space support for it.

On your machine, install `hox`, [uv](https://docs.astral.sh/uv/), and [pyinfra](https://pyinfra.com) (`uv tool install pyinfra`).

The VM needs a libvirt host you reach as root over SSH, with `virt-install`, `qemu-img` and `curl`, and room for the VM. Ours has 4 vCPUs, 8 GB of memory and 100 GB of disk; see [the node](../apps/README.md#the-node) for what that holds.

## Setting it up

1. Lay out a workspace. `hox` reads the first `hox.toml` in the current directory or its parents, so the scripts here find it when this directory sits inside the workspace:

   ```
   workspace/
     hox.toml     # from ../apps/hox.toml, or your own
     node/        # this directory
     slapos/      # the clone of your software releases' repository
   ```

2. Fill in `node/node.env`: the node's name, the partition count, and the VM's host, size and address (or an empty `KVM_HOST` for a machine of your own).
3. Edit `node/ssh_config` to match: `Host` is the node's name, `HostName` the node's address. Keep the `ProxyCommand` through the libvirt host for a VM on the host's private network; drop it for a machine with a public address.
4. In `hox.toml`, set `[node] title` to the node's name, and point `[node] ssh` at this `ssh_config`:

   ```toml
   [node]
   title = "my-node"
   ssh = ["ssh", "-F", "node/ssh_config", "-o", "LogLevel=ERROR", "my-node"]
   ```

5. Check what the VM step would do, without changing anything:

   ```
   node/create-vm.sh --check
   ```

## Running it

```
node/bootstrap.sh                # every software of hox.toml
node/bootstrap.sh radicale isso  # or only these
```

Then:

```
hox status       # the node, the builds, the instances
hox instances    # each instance, with state and URL
```

If a step fails, fix the cause and run `bootstrap.sh` again: what is done is skipped. With 8 GB of memory, run the big builds (Vikunja, Mattermost) on their own: `bootstrap.sh vikunja-source`, then the next one.

### Step by step

What `bootstrap.sh` runs, if you'd rather run it by hand (from `node/`):

```
./create-vm.sh
hox re6st-token                                  # prints the token
(umask 077; hox node-certificate > my-node.secret.json)
RE6ST_TOKEN=… SLAPOS_NODE=my-node.secret.json pyinfra -y inventory.py deploy.py
rm my-node.secret.json
hox deploy radicale                              # and so on for each software
```

`pyinfra` needs `RE6ST_TOKEN` and `SLAPOS_NODE` only when the node isn't registered yet; later runs (to update the packages, say) need neither. Add `--dry` to see what it would change.

Once `deploy.py` has run, the node has a global IPv6 address on `lo` (`ssh -F ssh_config my-node ip -6 addr show lo`). Its first attempt to reach a re6st peer often fails with "no peer returned"; a few minutes later a tunnel comes up and IPv6 to the internet works. On the master, the node appears in the project, and `hox status` shows it.

## Starting again on a new node

An instance stays on the node it was first requested on. When the node changes, do the following:

- give each software a new `instance` name in `hox.toml` (`radicale-2`, …): the old names belong to instances on the old node;
- once the new instances pass their checks, destroy the old ones (`hox destroy <old name>`) and, in the panel, invalidate the old node's Allocation Supplies and request the destruction of the old compute node.

A new node with a new name is the path we tested. Reusing the old name gets a new certificate for the same compute node (`hox node-certificate` revokes the old one), but we haven't tried what the master then does with the instances it still has on that node.

## When something goes wrong

- **re6st registration failed.** The token is used up. Destroy the token instance, so that the next run requests a new one: `hox destroy "re6st token for my-node"`. Then remove `/etc/re6stnet/re6stnet.conf` from the node if it was written, and run again.
- **The certificate is lost** (`bootstrap.sh` keeps `my-node.secret.json` until the node is configured, then removes it). Running again asks for a new one, which revokes the previous one: harmless before the node is configured, fatal for a node still using it.
- **The build fails on a download** from `raw.githubusercontent.com` ("Remote end closed connection"). Buildout fetches hundreds of small files, and GitHub sometimes drops one. SlapOS retries every minute; `hox deploy` waits for it.
- **The apt key of Nexedi's repository expired.** It does every couple of months. `deploy.py` downloads it again when it's more than a week old: run it again.
- **Instances have no URL.** The project has no access to the shared frontend: see "Before you start".

## Files

- `node.env`: the parameters, read by the two scripts and by `inventory.py`.
- `ssh_config`: how to reach the node as root, for `pyinfra`, the scripts and `hox`.
- `create-vm.sh`: the VM on the libvirt host (`--check` is read-only).
- `bootstrap.sh`: the whole sequence.
- `inventory.py`, `deploy.py`: the pyinfra inventory and operations.
- `register_with_certificate.py`: run on the node by `deploy.py`, to configure SlapOS with the certificate from `hox node-certificate`.

The installers' firewall check (Debian 12 has no firewall by default) and their branches for other distributions are not ported.
