"""The SlapOS master: its API (hateoas) for reads and forms, and the slap protocol."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx2

from .config import Master

TIMEOUT = 60


class MasterError(Exception):
    pass


def expand(template: str, params: dict[str, str | int | list]) -> str:
    """RFC 6570 form-style query expansion ({&a,b*}), the only kind the API uses."""

    def replace(match: re.Match) -> str:
        operator, names = match.group(1)[:1], match.group(1)[1:].split(",")
        if operator not in ("&", "?"):
            raise MasterError(f"unsupported URI template expression: {match.group(0)}")
        pairs = []
        for name in names:
            explode = name.endswith("*")
            name = name.rstrip("*")
            value = params.get(name)
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            if not explode and isinstance(value, list):
                values = [",".join(map(str, value))]
            pairs += [f"{name}={quote(str(v), safe='')}" for v in values]
        if not pairs:
            return ""
        return operator + "&".join(pairs)

    return re.sub(r"\{([^}]+)\}", replace, template)


class Api:
    def __init__(self, client: httpx2.Client, root: dict, tls_name: str | None = None):
        self._client = client
        self._extensions = {"sni_hostname": tls_name} if tls_name else {}
        self._root = root

    @classmethod
    def connect(cls, master: Master, transport: httpx2.BaseTransport | None = None) -> "Api":
        client = httpx2.Client(cert=(str(master.cert), str(master.key)), timeout=TIMEOUT, transport=transport)
        url = _get(client, urljoin(master.url, "getHateoasUrl")).text.strip()
        if urlsplit(url).scheme != "https":
            raise MasterError(f"getHateoasUrl returned {url!r}")
        extensions = {"sni_hostname": master.api_tls_name} if master.api_tls_name else {}
        root = _get(client, url, extensions).json()
        return cls(client, root, master.api_tls_name)

    def _get_json(self, url: str) -> dict:
        return _get(self._client, url, self._extensions).json()

    def _template(self, name: str) -> str:
        try:
            return self._root["_links"][name]["href"]
        except KeyError:
            raise MasterError(f"API root has no {name!r} link") from None

    def search(self, query: str, select: list[str], limit: int = 100) -> list[dict]:
        url = expand(self._template("raw_search"), {"query": query, "select_list": select, "limit": limit})
        rows = self._get_json(url)["_embedded"]["contents"]
        for row in rows:
            row.pop("_links", None)
        return rows

    def get(self, relative_url: str, view: str | None = None) -> dict:
        params = {"relative_url": relative_url}
        if view:
            params["view"] = view
        return self._get_json(expand(self._template("traverse"), params))


def _get(client: httpx2.Client, url: str, extensions: dict | None = None) -> httpx2.Response:
    try:
        response = client.get(url, extensions=extensions or {})
    except httpx2.TransportError as e:
        raise MasterError(f"GET {url}: {e}") from e
    if response.is_error:
        raise MasterError(f"{response.request.method} {response.url}: HTTP {response.status_code} {response.text[:200]}")
    return response


def quoted(value: str) -> str:
    """A value for an ERP5 catalog query (title:="...")."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# The slap protocol: form POSTs to master.url, some fields marshalled as
# slapos.core does (slapos.util.dumps, golden values in tests/test_master.py).


def marshal(value: dict[str, str] | str | bool) -> str:
    def text(s: str) -> str:
        # slapos.core escapes twice (xml_marshaller, then serialization); the master expects it.
        return f"<string>{escape(escape(s))}</string>"

    if isinstance(value, bool):
        body = f"<bool>{int(value)}</bool>"
    elif isinstance(value, str):
        body = text(value)
    elif not value:
        body = '<dictionary id="i2"/>'
    else:
        body = '<dictionary id="i2">' + "".join(text(k) + text(v) for k, v in value.items()) + "</dictionary>"
    return f"<marshal>{body}</marshal>"


_STATES = {"start_requested": "started", "stop_requested": "stopped", "destroy_requested": "destroyed"}


@dataclass(frozen=True)
class Request:
    """An instance request, as sent to requestComputerPartition."""

    software_release: str
    reference: str
    software_type: str
    filter: dict[str, str]
    parameters: dict[str, str] = field(default_factory=dict)
    shared: bool = False
    state: str = "started"

    def form(self) -> dict[str, str]:
        return {
            "software_release": self.software_release,
            "partition_reference": self.reference,
            "software_type": self.software_type,
            "partition_parameter_xml": marshal(self.parameters),
            "filter_xml": marshal(self.filter),
            "state": marshal(self.state),
            "shared_xml": marshal(self.shared),
        }


def stored_request(api: Api, reference: str) -> Request | None:
    """The request an instance tree was created with, read back from the master.

    The master answers 500 to a request whose URL, type or filter differ from
    the original: re-sending this one (with another state) is what works.
    """
    trees = api.search(
        f"portal_type:\"Instance Tree\" AND validation_state:validated AND title:={quoted(reference)}",
        ["url_string", "source_reference", "sla_xml", "text_content", "root_slave", "slap_state"],
    )
    if not trees:
        return None
    tree = trees[0]
    return Request(
        software_release=tree["url_string"],
        reference=reference,
        software_type=tree["source_reference"],
        filter=instance_xml(tree["sla_xml"]),
        parameters=instance_xml(tree["text_content"]),
        shared=bool(tree["root_slave"]),
        state=_STATES[tree["slap_state"]],
    )


def instance_xml(xml: str | None) -> dict[str, str]:
    """<instance><parameter id="k">v</parameter>…</instance> -> {k: v}."""
    if not xml:
        return {}
    root = ElementTree.fromstring(xml.encode())
    return {p.get("id"): p.text or "" for p in root.iter("parameter")}


class Slap:
    def __init__(self, client: httpx2.Client, url: str):
        self._client = client
        self._url = url

    @classmethod
    def connect(cls, master: Master, transport: httpx2.BaseTransport | None = None) -> "Slap":
        client = httpx2.Client(cert=(str(master.cert), str(master.key)), timeout=TIMEOUT, transport=transport)
        return cls(client, master.url)

    def _post(self, path: str, data: dict[str, str]) -> httpx2.Response:
        url = urljoin(self._url, path)
        try:
            return self._client.post(url, data=data)
        except httpx2.TransportError as e:
            raise MasterError(f"POST {url}: {e}") from e

    def supply(self, url: str, node: str, state: str = "available") -> None:
        response = self._post("supplySupply", {"url": url, "computer_id": node, "state": state})
        if response.is_error:
            raise MasterError(f"supply {url} on {node}: HTTP {response.status_code} {response.text[:200]}")

    def request(self, request: Request) -> bool:
        """Send an instance request. True: the master processed it; False: still provisioning (HTTP 408)."""
        response = self._post("requestComputerPartition", request.form())
        if response.status_code == 408:  # what the master answers while it provisions, not a timeout
            return False
        if response.status_code == 500:
            raise MasterError(
                f"request {request.reference}: HTTP 500. The master answers that to a request whose URL,"
                " software type or filter differ from the instance's original one."
            )
        if response.is_error:
            raise MasterError(f"request {request.reference}: HTTP {response.status_code} {response.text[:200]}")
        return True


def next_name(reference: str) -> str:
    """mynij-proxy-3 -> mynij-proxy-4; mynij-proxy -> mynij-proxy-2."""
    base, _, number = reference.rpartition("-")
    return f"{base}-{int(number) + 1}" if base and number.isdigit() else f"{reference}-2"
