#!/usr/bin/env bash
# From no VM to running, checked instances, with no panel visit:
#   1. create the VM on the libvirt host (create-vm.sh), if node.env names one;
#   2. for the registrations not done yet, get from the master the node's re6st
#      token (hox re6st-token) and its certificate (hox node-certificate, which
#      creates the compute node);
#   3. install and configure re6st and SlapOS on it (pyinfra deploy.py);
#   4. for each software: hox deploy (product, supply, request, then waits for
#      the allocation, the build and the checks).
#
#   ./bootstrap.sh [SOFTWARE…]   (default: every software of hox.toml)
#
# Run it from a workspace: hox.toml in this directory or a parent. Every step
# skips what is done: after a failure, fix it and run it again. A first build
# on an empty node takes hours.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"  # pyinfra's inventory names ssh_config by relative path
# shellcheck source=node.env
. ./node.env

node() { ssh -F ssh_config -o LogLevel=ERROR "$NODE" "$@"; }
step() { printf '\n== %s\n' "$*"; }

# Without KVM_HOST in node.env, the node is a machine of your own: no VM to create.
if [[ -n ${KVM_HOST:-} ]]; then
  step "VM"
  ./create-vm.sh
fi

step "SlapOS on $NODE"
# Only for a registration still to do.
registered() { [[ $(node "test -f $1 && echo yes || echo no") == yes ]]; }
# Keep the certificate until the node is configured, so a rerun after a
# failure reuses it rather than revoking it for a new one.
NODE_JSON=$HERE/$NODE.secret.json
registration=()
if ! registered /etc/re6stnet/re6stnet.conf; then registration+=("RE6ST_TOKEN=$(hox re6st-token)"); fi
if ! registered /etc/opt/slapos/slapos.cfg; then
  if [[ ! -s $NODE_JSON ]]; then
    # Two commands, not `a && b`: set -e doesn't stop on a failing `a`.
    (umask 077 && hox node-certificate >"$NODE_JSON.new")
    mv "$NODE_JSON.new" "$NODE_JSON"
  fi
  registration+=("SLAPOS_NODE=$NODE_JSON")
fi
env ${registration[@]+"${registration[@]}"} pyinfra -y inventory.py deploy.py  # bash 3.2: no "${a[@]}" when empty
rm -f "$NODE_JSON"

# The software to deploy, with its checkout and branch, from the hox.toml that
# hox reads (the first one in this directory or a parent).
software=$(uv run --quiet --no-project --python '>=3.12' python - "$@" <<'EOF'
import sys
import tomllib
from pathlib import Path

path = next(p / "hox.toml" for p in [Path.cwd(), *Path.cwd().parents] if (p / "hox.toml").exists())
table = tomllib.loads(path.read_text())["software"]
for name in sys.argv[1:] or table:
    sw = table[name]
    print(name, path.parent / sw["checkout"], sw["branch"])
EOF
)

# Each software's branch must exist locally: hox compares it with origin.
while read -r name checkout branch; do
  git -C "$checkout" fetch -q origin "$branch" </dev/null
  if ! git -C "$checkout" rev-parse -q --verify "refs/heads/$branch" >/dev/null; then
    git -C "$checkout" branch -q --track "$branch" "origin/$branch"
    echo "$name: created local branch $branch, tracking origin"
  fi
done <<<"$software"

# A for loop, not `while read` (shellcheck SC2013 is intended): ssh, under hox,
# would read the rest of the list from stdin.
for name in $(cut -d " " -f 1 <<<"$software"); do
  step "$name"
  hox deploy "$name"
done
