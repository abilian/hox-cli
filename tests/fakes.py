"""A fake master and node for the status tests, at unit and integration level."""

from __future__ import annotations

import dataclasses
import re

from conftest import BRANCH_URL, fixture_json

from hox_cli.node import NodeError, NodeFacts, Partition, SoftwareState, System

STALE_URL = BRANCH_URL.replace(
    "/mynij-proxy/software/mynij-proxy/", "/other/software/gone/"
)


class FakeProject:
    """A master with one known instance, one hox.toml doesn't name, and a destroyed one's row."""

    def search(self, query, select, limit=100):
        if "Software Installation" in query:
            return [
                {"url_string": BRANCH_URL, "slap_state": "start_requested"},
                {"url_string": STALE_URL, "slap_state": "destroy_requested"},
            ]
        if "Instance Tree" in query:
            return [
                {
                    "title": "zzz-1",
                    "slap_state": "stop_requested",
                    "url_string": STALE_URL,
                },
                {
                    "title": "mynij-proxy-3",
                    "slap_state": "start_requested",
                    "url_string": BRANCH_URL,
                },
            ]
        connection = {
            "url": "https://front.example",
            "monitor-setup-url": "https://monitor.example/#password=s3cret",
        }
        return [
            {
                "title": "mynij-proxy-2",
                "aggregate_title": "slappart0",
                "getConnectionXmlAsDict": connection,
            },
            {
                "title": "mynij-proxy-3",
                "aggregate_title": "slappart1",
                "getConnectionXmlAsDict": connection,
            },
            {"title": "zzz-1", "aggregate_title": None, "getConnectionXmlAsDict": None},
        ]


class FakeNode:
    def __init__(self, partitions=None, error=None):
        self.partitions, self.error, self.urls = partitions, error, None

    def system(self):
        if self.error:
            raise NodeError(self.error)
        return System(90000, self.partitions, 3700, 7900, 45056, 122880)

    def facts(self, urls):
        self.urls = urls
        return NodeFacts([SoftwareState(u, "building", "Error: x") for u in urls], [])


UP = [Partition("slappart0", 0, 0, []), Partition("slappart1", 300, 6, [])]


class FakeApi:
    """Answers searches from the recorded fixtures, by portal type."""

    def __init__(self, **rows):
        self.rows = {
            "Instance Tree": fixture_json("search_tree.json")["_embedded"]["contents"],
            "Software Instance": fixture_json("search_instances.json")["_embedded"][
                "contents"
            ],
            "Software Product Release Variation": fixture_json("search_release.json")[
                "_embedded"
            ]["contents"],
            "Allocation Supply": fixture_json("search_supply.json")["_embedded"][
                "contents"
            ],
            "Allocation Supply Line": [
                {"relative_url": "allocation_supply_module/20261029.023-A00FDF/1"}
            ],
        } | rows

    def search(self, query, select, limit=100):
        portal_type = re.search(r'portal_type:\(?"([^"]+)"', query).group(1)
        return [dict(r) for r in self.rows[portal_type]]

    def get(self, relative_url, view=None):
        assert (relative_url, view) == (
            "allocation_supply_module/20261029.023-A00FDF/1",
            "view",
        )
        return fixture_json("supply_line_view.json")


def unallocated(inst):
    return dataclasses.replace(inst, partition=None)
