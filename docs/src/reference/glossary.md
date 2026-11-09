# Glossary

This page explains the SlapOS terms these pages use, in alphabetical order.

**Allocation Supply**
: A record on the master that says which software releases, with which software types, may run on which compute nodes. The master allocates a partition to an instance only if its release is ticked in a validated Allocation Supply of the project. `hox setup` creates and fills it.

**Allocation scope**
: A setting of a compute node that opens it to new instances. `hox setup` opens it.

**Branch URL**
: The URL of a software release on a Git branch, `https://raw.githubusercontent.com/<repo>/<branch>/<path>`. The node rebuilds it in place after each push. See [Releases and instances](../guides/releases.md).

**Buildout**
: The tool SlapOS uses to build software releases and deploy instances, from profiles such as `software.cfg`.

**Compute node**
: A machine that builds software releases and runs instances, each in a partition.

**Connection parameters**
: The values an instance publishes once deployed, such as its addresses. Their names depend on the release: many publish `url` (the public address, through the frontend) and `backend-url` (the address on the node). Some hold credentials.

**Frontend**
: The service that publishes an instance at a public HTTPS address. On Rapid.Space, a shared frontend serves every project.

**Instance**
: A running copy of a software release, with a name, in a partition of a compute node.

**Master**
: The central SlapOS server: it records which software releases exist, which nodes can run them, and which instance runs where. Rapid.Space runs one at `slap.vifib.com`, with a web panel at `panel.rapid.space`.

**Partition**
: A directory of a compute node where one instance lives, with its processes, ports and data.

**Pinned URL**
: The URL of a software release at a given commit, which never changes. See [Releases and instances](../guides/releases.md).

**Project**
: A group of compute nodes, software products and instances on the master, with its members.

**Promise**
: A health check an instance declares, such as "the app answers on its port". The node runs the promises after each deployment and reports those that fail.

**re6st**
: The IPv6 mesh network that connects SlapOS nodes. A node joins it with a token. An instance's backend address is usually reachable only over re6st, which is why `hox check` runs on the node.

**slapgrid**
: The SlapOS agent on each compute node. Run by cron, `slapos node software` builds the releases supplied to the node, and `slapos node instance` deploys the node's instances.

**Software Product**
: A record on the master that groups the software releases of one application, with its software types.

**Software release**
: The description of how to build and run an application: a `software.cfg` file, identified by its URL.

**Software type**
: The variant of a software release an instance is deployed as. Usually `default`.

**Supply**
: To ask a compute node to build a software release.
