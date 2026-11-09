"""pyinfra inventory: the node of node.env, reached through ssh_config."""

import shlex
from pathlib import Path

params = dict(
    word.split("=", 1)
    for line in (Path(__file__).parent / "node.env").read_text().splitlines()
    for word in shlex.split(line, comments=True)
)

hosts = [
    (
        params["NODE"],
        {
            "ssh_config_file": "ssh_config",
            "computer_name": params["NODE"],
            "partitions": int(params["PARTITIONS"]),
        },
    ),
]
