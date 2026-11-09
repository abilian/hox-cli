"""The SlapOS master: its API (hateoas) for reads and forms, and the slap protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx2

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Master
    from collections.abc import Mapping

TIMEOUT = 60


class MasterError(Exception):
    pass


def expand(template: str, params: Mapping[str, str | int | list]) -> str:
    """RFC 6570 form-style query expansion ({&a,b*}), the only kind the API uses."""

    def replace(match: re.Match) -> str:
        operator, names = match.group(1)[:1], match.group(1)[1:].split(",")
        if operator not in {"&", "?"}:
            msg = f"unsupported URI template expression: {match.group(0)}"
            raise MasterError(msg)
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
    def connect(
        cls, master: Master, transport: httpx2.BaseTransport | None = None
    ) -> Api:
        client = httpx2.Client(
            cert=(str(master.cert), str(master.key)),
            timeout=TIMEOUT,
            transport=transport,
        )
        url = _get(client, urljoin(master.url, "getHateoasUrl")).text.strip()
        if urlsplit(url).scheme != "https":
            msg = f"getHateoasUrl returned {url!r}"
            raise MasterError(msg)
        extensions = (
            {"sni_hostname": master.api_tls_name} if master.api_tls_name else {}
        )
        root = _get(client, url, extensions).json()
        return cls(client, root, master.api_tls_name)

    def _get_json(self, url: str) -> dict:
        return _get(self._client, url, self._extensions).json()

    def _template(self, name: str) -> str:
        try:
            return self._root["_links"][name]["href"]
        except KeyError:
            msg = f"API root has no {name!r} link"
            raise MasterError(msg) from None

    def search(self, query: str, select: list[str], limit: int = 100) -> list[dict]:
        url = expand(
            self._template("raw_search"),
            {"query": query, "select_list": select, "limit": limit},
        )
        rows = self._get_json(url)["_embedded"]["contents"]
        for row in rows:
            row.pop("_links", None)
        return rows

    def get(self, relative_url: str, view: str | None = None) -> dict:
        params = {"relative_url": relative_url}
        if view:
            params["view"] = view
        return self._get_json(expand(self._template("traverse"), params))

    def act(
        self, relative_url: str, action: str, fields: dict[str, str] | None = None
    ) -> dict | None:
        """Submit one of a document's actions as the panel does: a fast input dialog
        (add_slapos_software_type), a workflow transition (validate_action) or a script.

        `fields` are keyed as the form keys them (field_your_software_type); every
        other field of the form is sent with its default. Returns the master's
        answer when it is JSON (a script may answer with a form of its results).
        """
        links = [
            link
            for name, value in self.get(relative_url, "view")["_links"].items()
            if name.startswith("action_")
            for link in (value if isinstance(value, list) else [value])
        ]
        href = next(
            (link["href"] for link in links if link.get("name") == action), None
        )
        if href is None:
            names = sorted({link.get("name") for link in links})
            msg = f"{relative_url} has no action {action!r}; it has: {', '.join(names)}"
            raise MasterError(msg)
        form = self._get_json(href)["_embedded"]["_view"]
        actions = form["_actions"]
        data = {
            f["key"]: f["default"] or ""
            for f in form.values()
            if isinstance(f, dict) and "key" in f
        }
        # The method is in _actions.put for dialogs, next to it for scripts.
        data["dialog_method"] = actions["put"].get("action") or actions["action"]
        return self._post(actions["put"]["href"], data | (fields or {}))

    def edit(
        self, relative_url: str, fields: dict[str, str], view: str = "view"
    ) -> None:
        """Change fields of one of a document's views, keyed as the form keys them (field_my_title).

        Base_edit skips the fields absent from the POST: the others keep their values.
        """
        form = self.get(relative_url, view)["_embedded"]["_view"]
        self._post(
            form["_actions"]["put"]["href"],
            {"form_id": form["form_id"]["default"], **fields},
        )

    def _post(self, url: str, data: dict) -> dict | None:
        try:
            response = self._client.post(
                url,
                data=data,
                extensions=self._extensions,
                headers={"Accept": "application/hal+json"},
            )
        except httpx2.TransportError as e:
            msg = f"POST {url}: {e}"
            raise MasterError(msg) from e
        if response.is_error:
            msg = f"POST {url}: HTTP {response.status_code} {response.text[:200]}"
            raise MasterError(msg)
        if "json" not in response.headers.get(
            "content-type", ""
        ):  # application/json or application/hal+json
            return None  # a redirect
        body = response.json()
        if body.get("portal_status_level") == "error":
            msg = f"POST {url}: {body.get('portal_status_message')}"
            raise MasterError(msg)
        return body


def _get(
    client: httpx2.Client, url: str, extensions: dict | None = None
) -> httpx2.Response:
    try:
        response = client.get(url, extensions=extensions or {})
    except httpx2.TransportError as e:
        msg = f"GET {url}: {e}"
        raise MasterError(msg) from e
    if response.is_error:
        msg = f"{response.request.method} {response.url}: HTTP {response.status_code} {response.text[:200]}"
        raise MasterError(msg)
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
        body = (
            '<dictionary id="i2">'
            + "".join(text(k) + text(v) for k, v in value.items())
            + "</dictionary>"
        )
    return f"<marshal>{body}</marshal>"


_STATES = {
    "start_requested": "started",
    "stop_requested": "stopped",
    "destroy_requested": "destroyed",
}


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
    project: str | None = (
        None  # without it, the master guesses (from the filter or the release)
    )

    def form(self) -> dict[str, str]:
        project = {"project_reference": self.project} if self.project else {}
        return project | {
            "software_release": self.software_release,
            "partition_reference": self.reference,
            "software_type": self.software_type,
            "partition_parameter_xml": marshal(self.parameters),
            "filter_xml": marshal(self.filter),
            "state": marshal(self.state),
            "shared_xml": marshal(self.shared),
        }


@dataclass(frozen=True)
class ComputeNode:
    reference: str
    relative_url: str


def nodes_titled(api: Api, project: str, title: str) -> list[ComputeNode]:
    rows = api.search(
        f'portal_type:"Compute Node" AND validation_state:validated AND follow_up_reference:={quoted(project)}'
        f" AND title:={quoted(title)}",
        ["reference", "relative_url"],
    )
    return [ComputeNode(r["reference"], r["relative_url"]) for r in rows]


def find_node(api: Api, project: str, title: str) -> ComputeNode:
    """The compute node, by its title in the project (its reference changes with each registration)."""
    nodes = nodes_titled(api, project, title)
    if len(nodes) != 1:
        found = f"{len(nodes)} of them" if nodes else "none (`hox node-certificate`)"
        msg = f"compute node {title!r} in {project}: {found}"
        raise MasterError(msg)
    return nodes[0]


def stored_request(api: Api, reference: str) -> Request | None:
    """The request an instance tree was created with, read back from the master.

    The master answers 500 to a request whose URL, type or filter differ from
    the original: re-sending this one (with another state) is what works.
    """
    trees = api.search(
        f'portal_type:"Instance Tree" AND validation_state:validated AND title:={quoted(reference)}',
        [
            "url_string",
            "source_reference",
            "sla_xml",
            "text_content",
            "root_slave",
            "slap_state",
            "follow_up_reference",
        ],
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
        project=tree["follow_up_reference"],
    )


def instance_xml(xml: str | None) -> dict[str, str]:
    """<instance><parameter id="k">v</parameter>…</instance> -> {k: v}."""
    if not xml:
        return {}
    params = {}
    for p in ElementTree.fromstring(xml.encode()).iter("parameter"):
        key = p.get("id")
        if key is None:
            msg = f"instance XML: a parameter has no id: {xml[:200]}"
            raise MasterError(msg)
        params[key] = p.text or ""
    return params


class Slap:
    def __init__(self, client: httpx2.Client, url: str):
        self._client = client
        self._url = url

    @classmethod
    def connect(
        cls, master: Master, transport: httpx2.BaseTransport | None = None
    ) -> Slap:
        client = httpx2.Client(
            cert=(str(master.cert), str(master.key)),
            timeout=TIMEOUT,
            transport=transport,
        )
        return cls(client, master.url)

    def _post(self, path: str, data: dict[str, str]) -> httpx2.Response:
        url = urljoin(self._url, path)
        try:
            return self._client.post(url, data=data)
        except httpx2.TransportError as e:
            msg = f"POST {url}: {e}"
            raise MasterError(msg) from e

    def supply(self, url: str, node: str, state: str = "available") -> None:
        response = self._post(
            "supplySupply", {"url": url, "computer_id": node, "state": state}
        )
        if response.is_error:
            msg = f"supply {url} on {node}: HTTP {response.status_code} {response.text[:200]}"
            raise MasterError(msg)

    def request(self, request: Request) -> bool:
        """Send an instance request. True: the master processed it; False: still provisioning (HTTP 408)."""
        response = self._post("requestComputerPartition", request.form())
        if (
            response.status_code == 408
        ):  # what the master answers while it provisions, not a timeout
            return False
        if response.status_code == 500:
            msg = (
                f"request {request.reference}: HTTP 500. The master answers that to a request whose URL,"
                " software type or filter differ from the instance's original one."
            )
            raise MasterError(msg)
        if response.is_error:
            msg = f"request {request.reference}: HTTP {response.status_code} {response.text[:200]}"
            raise MasterError(msg)
        return True


def next_name(reference: str) -> str:
    """my-app-3 -> my-app-4; my-app -> my-app-2."""
    base, _, number = reference.rpartition("-")
    return (
        f"{base}-{int(number) + 1}" if base and number.isdigit() else f"{reference}-2"
    )
