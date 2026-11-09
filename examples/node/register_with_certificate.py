"""`slapos node register`, with a certificate instead of a token.

Runs on the node with the slapos package's interpreter:

    /opt/slapos/bin/py register_with_certificate.py NODE_JSON [register options…] NODE_NAME

NODE_JSON is what `hox node-certificate` prints: {"reference", "certificate", "key"}.
The register options are slapos node register's own (--master-url,
--interface-name, --partition-number, …), with its defaults: this is its code,
minus the certificate download that needs the token.
"""

import json
import logging
import sys

from slapos.cli.register import (
    RegisterCommand,
    RegisterConfig,
    save_former_config,
    slapconfig,
)

SLAPOS_CONFIGURATION = "/etc/opt/slapos"

logging.basicConfig(level=logging.INFO, format="%(message)s")
node = json.load(open(sys.argv[1]))
args = (
    RegisterCommand(None, None)
    .get_parser("slapos node register")
    .parse_args(sys.argv[2:])
)

conf = RegisterConfig(logger=logging.getLogger("register"))
conf.setConfig(args)
conf.COMPConfig(
    slapos_configuration=SLAPOS_CONFIGURATION,
    computer_id=node["reference"],
    certificate=node["certificate"],
    key=node["key"],
)
save_former_config(conf)
slapconfig(conf)
conf.logger.info("Node configured as %s.", node["reference"])
