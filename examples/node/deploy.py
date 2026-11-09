"""Turn a Debian 12 host into a SlapOS compute node registered on Rapid.Space.

Port of Nexedi's gnet-re6stnet.yml and slapos.yml playbooks (nexedi/slapos.package,
run by deploy.rapid.space/gnet/re6st and deploy.rapid.space/slapos), without prompts.

    RE6ST_TOKEN=… SLAPOS_NODE=my-node.secret.json pyinfra -y inventory.py deploy.py

The two variables are needed only for a registration still to do (see README.md).
"""

import os

from pyinfra import host
from pyinfra.facts.files import File
from pyinfra.facts.hardware import Cpus
from pyinfra.operations import apt, files, server, systemd

OBS = "https://download.opensuse.org/repositories/home:/VIFIBnexedi/Debian_12"
KEYRING = "/etc/apt/keyrings/vifibnexedi.asc"
# Freefib ("free network") registry, as in gnet-re6stnet.yml / settings/gnet.yml.
# Tokens from the "Re6st Token" software type only work with this registry.
RE6ST_REGISTRY = "http://re6stnet.gnet.erp5.cn/"
RE6ST_FINGERPRINT = (
    "sha256:41f22e190c7c3e910ffaafd8a9e89f8ad6ea6f4236950a5d8125db37c97c7fac"
)
MASTER_URL = "https://slap.vifib.com/"
MASTER_URL_WEB = "https://panel.rapid.space/"
RE6ST_CONF = "/etc/re6stnet/re6stnet.conf"
SLAPOS_CFG = "/etc/opt/slapos/slapos.cfg"
SLAPOS_CRON = "/etc/cron.d/slapos-node"
SHARED_PARTS = "/opt/slapgrid/shared"
SLAPOS_PY = "/opt/slapos/bin/py"  # the slapos package's interpreter, slapos on its path
HERE = os.path.dirname(os.path.abspath(__file__))


def registration(name):
    """RE6ST_TOKEN, SLAPOS_NODE: only needed on the first run, when the host isn't registered yet."""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"{name} is required to register this host (not configured yet)"
        )
    return value


apt.packages(
    name="Base packages",
    packages=["gpg", "chrony", "cron", "lsb-release"],
    update=True,
    cache_time=3600,
)

# The OBS key expires every couple of months: refresh it weekly.
files.download(
    name="Nexedi OBS repository key",
    src=f"{OBS}/Release.key",
    dest=KEYRING,
    cache_time=7 * 24 * 3600,
)
apt.repo(
    name="Nexedi OBS repository",
    src=f"deb [signed-by={KEYRING}] {OBS}/ ./",
    filename="vifibnexedi",
)
apt.packages(
    name="re6st and SlapOS node",
    packages=["re6st-node", "slapos-node"],
    update=True,
)

server.sysctl(
    name="Enable IPv6",
    key="net.ipv6.conf.all.disable_ipv6",
    value=0,
    persist=True,
)

# re6st
if not host.get_fact(File, path=RE6ST_CONF):
    server.shell(
        name="Register on the re6st network",
        commands=[
            f"re6st-conf --registry {RE6ST_REGISTRY} --token {registration('RE6ST_TOKEN')}"
            f" -r title {host.data.computer_name} --fingerprint {RE6ST_FINGERPRINT}"
            " -d /etc/re6stnet"
        ],
    )

socket_flags = files.line(
    name="re6st socket flags", path=RE6ST_CONF, line="O--socket-flags"
)
tcp_nodelay = files.line(
    name="re6st TCP_NODELAY", path=RE6ST_CONF, line='O"TCP_NODELAY"'
)

systemd.service(
    name="re6stnet running",
    service="re6stnet",
    running=True,
    enabled=True,
    restarted=socket_flags.did_change or tcp_nodelay.did_change,
)

# SlapOS node. `slapos node register` needs a token from panel.rapid.space; we
# have the node's certificate instead (`hox node-certificate`, in SLAPOS_NODE), and
# run register's own code with it.
if not host.get_fact(File, path=SLAPOS_CFG):
    files.put(
        name="Node certificate",
        src=registration("SLAPOS_NODE"),
        dest="/root/slapos-node.json",
        mode="600",
    )
    files.put(
        name="Register script",
        src=os.path.join(HERE, "register_with_certificate.py"),
        dest="/root/register_with_certificate.py",
    )
    server.shell(
        name="Configure the SlapOS node",
        commands=[
            f"{SLAPOS_PY} /root/register_with_certificate.py /root/slapos-node.json"
            f" --master-url {MASTER_URL} --master-url-web {MASTER_URL_WEB}"
            f" --interface-name lo --partition-number {host.data.partitions}"
            f" {host.data.computer_name}",
            "rm /root/slapos-node.json /root/register_with_certificate.py",
        ],
    )

files.replace(
    name="Partition count",
    path=SLAPOS_CFG,
    text="^partition_amount = .*$",
    replace=f"partition_amount = {host.data.partitions}",
)

# Creates slapsoft, the software user, among others.
server.shell(
    name="Format partitions",
    commands=["slapos node format --now --alter_user=True"],
)

# Builds use one CPU unless MAKEFLAGS says otherwise: slapos.recipe.cmmi passes
# no -j (a cold build that includes LLVM takes 8+ hours that way). Next to
# MAILTO: a cron variable only applies to the entries after it.
jobs = host.get_fact(Cpus)
server.shell(
    name="Parallel builds",
    commands=[f"sed -i '/^MAKEFLAGS=/d; /^MAILTO=/a MAKEFLAGS=-j{jobs}' {SLAPOS_CRON}"],
)

# Without shared parts, every new software release URL recompiles the whole
# stack (~45 min on 1 vCPU). With them, components are built once and reused.
files.directory(
    name="Shared parts directory",
    path=SHARED_PARTS,
    user="slapsoft",
    group="slapsoft",
)
files.replace(
    name="Enable shared parts",
    path=SLAPOS_CFG,
    text="^#shared_part_list =$",
    replace=f"shared_part_list = {SHARED_PARTS}",
)
