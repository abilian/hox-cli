import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

BRANCH_URL = "https://raw.githubusercontent.com/abilian/slapos_abilian_edge/mynij-proxy/software/mynij-proxy/software.cfg"

CONFIG = {
    "master": {
        "url": "https://slap.vifib.com/",
        "cert": "~/.slapos/client.crt",
        "key": "~/.slapos/client.key",
        "api_tls_name": "slap.vifib.com",
    },
    "project": {"reference": "PROJ-20261026.009-F09D5B"},
    "node": {"reference": "COMP-20261027.021-9D1365", "ssh": ["ssh", "-F", "node-deployer/ssh_config", "hox-node"]},
    "software": {
        "mynij-proxy": {
            "repo": "abilian/slapos_abilian_edge",
            "checkout": "slapos_abilian_edge",
            "branch": "mynij-proxy",
            "path": "software/mynij-proxy/software.cfg",
            "software_type": "default",
            "product": "mynij-proxy",
            "supply_title": "mynij-proxy on abilian-hox-node-1",
            "instance": "mynij-proxy-3",
            "checks": [{"name": "backend ping", "url": "{backend-url}/ping", "expect_status": 200}],
        }
    },
}


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def config_data() -> dict:
    return json.loads(json.dumps(CONFIG))  # a deep copy tests can modify
