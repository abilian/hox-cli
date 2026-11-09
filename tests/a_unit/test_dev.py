from __future__ import annotations

import pytest

from hox_cli.config import Check
from hox_cli.dev import curl_command, evaluate, run_checks

PING = Check("ping", "{backend-url}/ping", expect_status=200)
CORS = Check(
    "CORS",
    "{backend-url}/proxy",
    headers={"Origin": "https://e.org"},
    expect_header={"Access-Control-Allow-Origin": "https://e.org"},
)
HEADERS = "HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\naccess-control-allow-origin: https://e.org\r\n\r\ncurl-exit=0\n"


def test_curl_command_quotes_and_keeps_its_exit_code():
    assert curl_command(CORS, "https://[::1]:3001/proxy?a=b&c") == (
        "curl -gsk -o /dev/null -D - --max-time 20 -H 'Origin: https://e.org' 'https://[::1]:3001/proxy?a=b&c'"
        '; echo "curl-exit=$?"'
    )


@pytest.mark.parametrize(
    "check, output, ok, detail",
    [
        (PING, HEADERS, True, "status 200"),
        (
            PING,
            HEADERS.replace("200 OK", "502 Bad Gateway"),
            False,
            "status 502, expected 200",
        ),
        (CORS, HEADERS, True, "status 200"),
        (
            CORS,
            HEADERS.replace("access-control-allow-origin: https://e.org\r\n", ""),
            False,
            "Access-Control-Allow-Origin: None, expected 'https://e.org'",
        ),
        (PING, "curl-exit=7\n", False, "curl exit 7"),
    ],
)
def test_evaluate(check, output, ok, detail):
    result = evaluate(check, "u", output)
    assert (result.ok, result.detail) == (ok, detail)


class FakeNode:
    def __init__(self, output):
        self.output, self.commands = output, []

    def run(self, command):
        self.commands.append(command)
        return self.output


def test_run_checks_fills_connection_parameters_and_skips_unpublished():
    frontend = Check("frontend", "{url}/ping", expect_status=200)
    node = FakeNode(HEADERS)
    results = run_checks(
        (PING, frontend), {"backend-url": "https://[::1]:3001", "url": "None"}, node
    )
    assert [(r.name, r.ok) for r in results] == [("ping", True), ("frontend", False)]
    assert results[1].detail == "url is not published yet"
    assert node.commands == [curl_command(PING, "https://[::1]:3001/ping")]
