from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

# pyrefly: ignore[missing-import]
from conftest import fixture_json

from hox_cli.config import Master
from hox_cli.master import (
    Api,
    ComputeNode,
    MasterError,
    Request,
    Slap,
    expand,
    find_node,
    instance_xml,
    marshal,
    next_name,
    quoted,
    stored_request,
)

SEARCH = "https://api.vifib.com/ERP5Document_getHateoas?mode=search{&query,select_list*,limit*,group_by*}"
MASTER = Master(
    "https://slap.vifib.com/", Path("/c.crt"), Path("/c.key"), "slap.vifib.com"
)


def test_expand_explodes_lists_and_skips_missing():
    url = expand(
        SEARCH,
        {"query": 'title:="a b"', "select_list": ["title", "url_string"], "limit": 5},
    )
    assert url == (
        "https://api.vifib.com/ERP5Document_getHateoas?mode=search"
        "&query=title%3A%3D%22a%20b%22&select_list=title&select_list=url_string&limit=5"
    )


def test_expand_rejects_other_operators():
    with pytest.raises(MasterError, match="unsupported"):
        expand("https://x/{path}", {"path": "a"})


def test_quoted():
    assert quoted('a "b" \\') == '"a \\"b\\" \\\\"'


def api_with(handler) -> tuple[Api, list[httpx2.Request]]:
    seen = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path == "/getHateoasUrl":
            return httpx2.Response(200, text="https://api.vifib.com/")
        if request.url.path == "/" and request.url.host == "api.vifib.com":
            return httpx2.Response(200, json=fixture_json("root.json"))
        return handler(request)

    # Api.connect builds its own client: give it the mock transport.
    return Api.connect(MASTER, transport=httpx2.MockTransport(record)), seen


def test_connect_discovers_api_and_verifies_it_as_slap(monkeypatch):
    api, seen = api_with(lambda r: httpx2.Response(404))
    assert [str(r.url) for r in seen] == [
        "https://slap.vifib.com/getHateoasUrl",
        "https://api.vifib.com/",
    ]
    assert "sni_hostname" not in seen[0].extensions
    assert seen[1].extensions["sni_hostname"] == "slap.vifib.com"


def test_search_returns_rows_without_links():
    def handler(request):
        q = parse_qs(urlsplit(str(request.url)).query)
        assert q["mode"] == ["search"]
        assert q["select_list"] == ["title", "slap_state"]
        assert request.extensions["sni_hostname"] == "slap.vifib.com"
        return httpx2.Response(200, json=fixture_json("search_tree.json"))

    api, _ = api_with(handler)
    rows = api.search('portal_type:"Instance Tree"', ["title", "slap_state"])
    assert rows[0]["title"] == "mynij-proxy-3"
    assert "_links" not in rows[0]


def test_get_passes_view():
    def handler(request):
        q = parse_qs(urlsplit(str(request.url)).query)
        assert q == {"mode": ["traverse"], "relative_url": ["a/1"], "view": ["view"]}
        return httpx2.Response(200, json={"ok": 1})

    api, _ = api_with(handler)
    assert api.get("a/1", "view") == {"ok": 1}


def test_http_error_is_master_error():
    api, _ = api_with(lambda r: httpx2.Response(500, text="boom"))
    with pytest.raises(MasterError, match="HTTP 500 boom"):
        api.get("a/1")


def test_transport_error_is_master_error():
    def handler(request):
        raise httpx2.ConnectError("refused", request=request)

    api, _ = api_with(handler)
    with pytest.raises(MasterError, match="refused"):
        api.get("a/1")


# slapos.core 1.26.1's slapos.util.dumps on the same values.
GOLDEN = {
    "empty": ({}, '<marshal><dictionary id="i2"/></marshal>'),
    "filter": (
        {"computer_guid": "COMP-20261027.021-9D1365"},
        '<marshal><dictionary id="i2"><string>computer_guid</string>'
        "<string>COMP-20261027.021-9D1365</string></dictionary></marshal>",
    ),
    "two": (
        {"a": "x", "b": "y"},
        '<marshal><dictionary id="i2"><string>a</string><string>x</string>'
        "<string>b</string><string>y</string></dictionary></marshal>",
    ),
    "escape": (
        {"k&<": "v>&\"'"},
        '<marshal><dictionary id="i2"><string>k&amp;amp;&amp;lt;</string>'
        "<string>v&amp;gt;&amp;amp;\"'</string></dictionary></marshal>",
    ),
    "started": ("started", "<marshal><string>started</string></marshal>"),
    "false": (False, "<marshal><bool>0</bool></marshal>"),
    "true": (True, "<marshal><bool>1</bool></marshal>"),
}


@pytest.mark.parametrize("value, expected", GOLDEN.values(), ids=GOLDEN.keys())
def test_marshal_matches_slapos_core(value, expected):
    assert marshal(value) == expected


BRANCH_URL = "https://raw.githubusercontent.com/abilian/slapos_abilian_edge/mynij-proxy/software/mynij-proxy/software.cfg"


class Searching:
    def search(self, query, select, limit=100):
        assert 'title:="mynij-proxy-3"' in query
        return fixture_json("search_tree_request.json")["_embedded"]["contents"]


def test_stored_request_reads_back_the_original():
    # pyrefly: ignore[bad-argument-type]
    assert stored_request(Searching(), "mynij-proxy-3") == Request(
        software_release=BRANCH_URL,
        reference="mynij-proxy-3",
        software_type="default",
        filter={"computer_guid": "COMP-20261027.021-9D1365"},
        parameters={},
        shared=False,
        state="started",
        project="PROJ-20261026.009-F09D5B",
    )


def test_instance_xml():
    assert instance_xml(None) == {}
    assert instance_xml(
        '<?xml version="1.0"?><instance><parameter id="a">1</parameter>'
        '<parameter id="b"/></instance>'
    ) == {"a": "1", "b": ""}


def slap_with(status: int, seen: list) -> Slap:
    def handler(request):
        seen.append((request.url.path, parse_qs(request.content.decode())))
        return httpx2.Response(status, text="<marshal/>")

    return Slap.connect(MASTER, transport=httpx2.MockTransport(handler))


REQUEST = Request(BRANCH_URL, "mynij-proxy-3", "default", {"computer_guid": "COMP-1"})


def test_request_payload():
    seen = []
    assert slap_with(200, seen).request(REQUEST) is True
    path, form = seen[0]
    assert path == "/requestComputerPartition"
    assert form == {
        "software_release": [BRANCH_URL],
        "partition_reference": ["mynij-proxy-3"],
        "software_type": ["default"],
        "partition_parameter_xml": [marshal({})],
        "filter_xml": [marshal({"computer_guid": "COMP-1"})],
        "state": [marshal("started")],
        "shared_xml": [marshal(False)],
    }


def test_request_408_means_provisioning():
    assert slap_with(408, []).request(REQUEST) is False


def test_request_500_explains_the_master():
    with pytest.raises(MasterError, match="differ from the instance's original"):
        slap_with(500, []).request(REQUEST)


def test_supply_payload():
    seen = []
    slap_with(200, seen).supply(BRANCH_URL, "COMP-1", "destroyed")
    assert seen == [
        (
            "/supplySupply",
            {"url": [BRANCH_URL], "computer_id": ["COMP-1"], "state": ["destroyed"]},
        )
    ]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("mynij-proxy-3", "mynij-proxy-4"),
        ("mynij-proxy", "mynij-proxy-2"),
        ("x-9", "x-10"),
    ],
)
def test_next_name(name, expected):
    assert next_name(name) == expected


DOC = {
    "_links": {
        "action_object_view": [{"name": "view", "href": "https://api.vifib.com/v"}],
        "action_object_jio_fast_input": [
            {
                "name": "add_slapos_software_type",
                "href": "https://api.vifib.com/dialog",
            },
        ],
    },
    "_embedded": {
        "_view": {
            "_actions": {
                "put": {
                    "action": "Base_edit",
                    "href": "https://api.vifib.com/doc/Base_edit",
                }
            },
            "form_id": {"key": "form_id", "default": "SoftwareProduct_view"},
        }
    },
}
DIALOG = {
    "_embedded": {
        "_view": {
            "_actions": {
                "put": {
                    "action": "SoftwareProduct_addSlapOSSoftwareType",
                    "href": "https://api.vifib.com/doc/Base_callDialogMethod",
                }
            },
            "dialog_id": {
                "key": "dialog_id",
                "default": "SoftwareProduct_viewSlapOSSoftwareTypeFastInputDialog",
            },
            "form_id": {"key": "form_id", "default": "SoftwareProduct_view"},
            "your_software_type": {"key": "field_your_software_type", "default": ""},
        }
    }
}
SCRIPT = {
    "_embedded": {
        "_view": {
            "_actions": {
                "put": {
                    "href": "https://api.vifib.com/project/Project_addSlapOSAllocationSupply"
                },
                "action": "Project_addSlapOSAllocationSupply",
                "method": "POST",
            },
            "form_id": {
                "key": "form_id",
                "default": "Base_viewFakePythonScriptActionForm",
            },
        }
    }
}


def forms_api(dialog: dict, reply: httpx2.Response):
    posts = []

    def handler(request):
        if request.method == "POST":
            assert request.extensions["sni_hostname"] == "slap.vifib.com"
            posts.append((str(request.url), parse_qs(request.content.decode())))
            return reply
        return httpx2.Response(
            200, json=dialog if request.url.path == "/dialog" else DOC
        )

    return api_with(handler)[0], posts


OK = httpx2.Response(
    201,
    json={
        "portal_status_message": "New Software Type created.",
        "portal_status_level": "success",
    },
)


def test_act_submits_the_dialog_with_its_defaults():
    api, posts = forms_api(DIALOG, OK)
    api.act("doc", "add_slapos_software_type", {"field_your_software_type": "hox-test"})
    assert posts == [
        (
            "https://api.vifib.com/doc/Base_callDialogMethod",
            {
                "dialog_id": ["SoftwareProduct_viewSlapOSSoftwareTypeFastInputDialog"],
                "form_id": ["SoftwareProduct_view"],
                "field_your_software_type": ["hox-test"],
                "dialog_method": ["SoftwareProduct_addSlapOSSoftwareType"],
            },
        )
    ]


def test_act_runs_a_script_action():
    api, posts = forms_api(
        SCRIPT, httpx2.Response(302, headers={"location": "https://api.vifib.com/x"})
    )
    api.act("project", "add_slapos_software_type")
    assert (
        posts[0][0] == "https://api.vifib.com/project/Project_addSlapOSAllocationSupply"
    )
    assert posts[0][1]["dialog_method"] == ["Project_addSlapOSAllocationSupply"]


def test_act_returns_a_form_answered_as_hal_json():
    # What a script that shows its results answers (Project_addSlapOSComputeNode).
    form = {"your_compute_node_reference": {"default": "COMP-42"}}
    reply = httpx2.Response(
        200, content=json.dumps(form), headers={"content-type": "application/hal+json"}
    )
    api, _ = forms_api(SCRIPT, reply)
    assert api.act("project", "add_slapos_software_type") == form


def test_act_unknown_action_lists_the_known_ones():
    api, _ = forms_api(DIALOG, OK)
    with pytest.raises(
        MasterError,
        match="no action 'validate_action'; it has: add_slapos_software_type, view",
    ):
        api.act("doc", "validate_action")


def test_act_reports_the_masters_error_message():
    api, _ = forms_api(
        DIALOG,
        httpx2.Response(
            200, json={"portal_status_message": "Nope.", "portal_status_level": "error"}
        ),
    )
    with pytest.raises(MasterError, match="Nope."):
        api.act("doc", "add_slapos_software_type")


def test_edit_posts_only_the_given_fields():
    api, posts = forms_api(DIALOG, httpx2.Response(200, json={}))
    api.edit("doc", {"field_my_description": "x"})
    assert posts == [
        (
            "https://api.vifib.com/doc/Base_edit",
            {"form_id": ["SoftwareProduct_view"], "field_my_description": ["x"]},
        )
    ]


def test_request_into_another_project():
    seen = []
    slap_with(200, seen).request(
        Request(BRANCH_URL, "t", "default", {}, shared=True, project="PROJ-112")
    )
    form = seen[0][1]
    assert form["project_reference"] == ["PROJ-112"]
    assert form["shared_xml"] == [marshal(True)]


def test_request_without_project_lets_the_master_choose():
    seen = []
    slap_with(200, seen).request(REQUEST)
    assert "project_reference" not in seen[0][1]


class Nodes:
    def __init__(self, rows):
        self.rows = rows

    def search(self, query, select, limit=100):
        assert 'follow_up_reference:="P" AND title:="hox-node"' in query
        return self.rows


def test_find_node():
    assert find_node(
        Nodes([
            {"reference": "COMP-9", "relative_url": "compute_node_module/9"}
        ]),  # pyrefly: ignore[bad-argument-type]
        "P",
        "hox-node",
    ) == (ComputeNode("COMP-9", "compute_node_module/9"))


@pytest.mark.parametrize(
    "rows, found",
    [([], "none"), ([{"reference": "C1", "relative_url": "c/1"}] * 2, "2 of them")],
)
def test_find_node_needs_exactly_one(rows, found):
    with pytest.raises(MasterError, match=f"compute node 'hox-node' in P: {found}"):
        # pyrefly: ignore[bad-argument-type]
        find_node(Nodes(rows), "P", "hox-node")
