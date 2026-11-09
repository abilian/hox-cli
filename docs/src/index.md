# hox

`hox` is a command-line tool for deploying your applications on SlapOS, an open-source cloud system, and for working on them once they run. You describe your applications in one configuration file. From then on, deploying, updating and checking them takes one command each, with no web forms to fill in.

## SlapOS in a few words

[SlapOS](https://slapos.nexedi.com) is an open-source cloud system made by Nexedi. Unlike most cloud systems, it doesn't use containers: it builds each application from source and runs it on compute nodes, which can be anyone's machines. [Rapid.Space](https://rapid.space) runs a public SlapOS cloud, and is the one `hox` works with.

A few words come up everywhere in SlapOS, and in these pages:

- A **software release** describes how to build and run an application. It is a `software.cfg` file in a Git repository, and the file's URL identifies it.
- The **master** is the central server. It knows which applications exist, which machines can run them and what runs where. Rapid.Space runs one, and has a web panel to drive it.
- A **compute node** is a machine that runs applications. It builds the software releases it is asked to, and keeps each application in a **partition**: a directory with separate processes and ports.
- An **instance** is one running copy of an application, with a name. You request an instance from the master; the master picks a partition on a node; the node starts the instance.
- The **frontend** gives each instance a public HTTPS address.

The [glossary](reference/glossary.md) has the other terms you will meet.

## What hox does for you

With SlapOS alone, deploying an application means filling in several forms in the web panel, then typing long commands that repeat the release URL every time, and logging in to the node over SSH to see whether the build worked. `hox` does all of that from your terminal:

```
hox deploy my-app       # from nothing to a running, checked application
hox rebuild my-app      # after a git push: rebuild, redeploy, check
hox status my-app       # what's wrong, and why
hox stop my-app-1       # stop an instance by its name
```

- **Deploy in one command.** `hox deploy` does the web panel's setup for you, asks the master for an instance, waits for the node to build and start it, then checks that the application answers.
- **Update after each change.** Push your changes to Git, then run `hox rebuild`: it rebuilds the application on the node and redeploys it, so you see the result in minutes.
- **Know what's going on.** `hox status` gathers what the master, the node and GitHub know: whether a build failed and why, which processes are down, why an instance hasn't started.
- **Manage instances by name.** Stop, start or destroy an instance by name alone. With SlapOS's `slapos` command, you would have to repeat the release URL, type and node exactly, or the master refuses.
- **Check your applications.** You list in the configuration what "working" means for each application, such as a page that must answer with status 200, and `hox check` tests it.

## Bring Hop3 apps to SlapOS

[Hop3](https://hop3.cloud) is an open-source platform for deploying web applications on a server you control. Its catalog has about 30 ready-to-run applications, and more are coming. Each one is described in a short `hop3.toml` file. `hox port` turns one of them into a SlapOS software release, so the catalog's applications can run on SlapOS too. See [Porting Hop3 apps](guides/porting-hop3.md).

## What you need

`hox` is for a developer, or a coding agent, working on a few applications on one SlapOS compute node. You need:

- an account on Rapid.Space, with a project, and its client certificate;
- a compute node in that project, with root SSH access to it ([Setting up a node](guides/new-node.md) shows what `hox` does to help);
- your software releases in a GitHub repository you can push to.

## Where to go next

- [Installation](get-started/installation.md), then [Configuration](get-started/configuration.md) and your [first deploy](get-started/first-deploy.md).
- [The development loop](guides/dev-loop.md), for the daily work.
- [Commands](reference/cli.md) and [`hox.toml`](reference/config.md), for reference.

`hox` was written by [Abilian](https://abilian.com) for [HyperOpen-X](https://lab.abilian.com/Projects/HOX/), a French public research project on an open, sovereign cloud. Abilian uses it to deploy its applications, and those of the Hop3 catalog, on SlapOS.
