from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from conftest import fixture_json
from hox_cli.config import Master
from hox_cli.master import Api, MasterError, expand, quoted

SEARCH = "https://api.vifib.com/ERP5Document_getHateoas?mode=search{&query,select_list*,limit*,group_by*}"
MASTER = Master("https://slap.vifib.com/", Path("/c.crt"), Path("/c.key"), "slap.vifib.com")


def test_expand_explodes_lists_and_skips_missing():
    url = expand(SEARCH, {"query": 'title:="a b"', "select_list": ["title", "url_string"], "limit": 5})
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
    assert [str(r.url) for r in seen] == ["https://slap.vifib.com/getHateoasUrl", "https://api.vifib.com/"]
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
