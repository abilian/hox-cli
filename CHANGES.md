# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First version.

### Added

- Deploying: `deploy`, `setup`, `release`, `supply`, `request`, `stop`, `start`, `destroy`, with instances managed by name.
- Developing: `rebuild` (rebuild a branch in place on the node, redeploy, check) and `try-start` (start a test build's processes without the master).
- Seeing: `instances`, `status`, `check`, `logs`, `build-report`, and `api get` / `api search` for raw access to the master's API.
- Setting up a node: `node-certificate` and `re6st-token`.
- Offline tools: `versions` (buildout pins from a `uv.lock`) and `port` (a SlapOS software release from a Hop3 recipe: Python, Go, Node and PHP apps, with PostgreSQL or MariaDB).
- Documentation site, built with Zensical.
