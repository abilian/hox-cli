"""The SlapOS master: its API (hateoas, ERP5Document_getHateoas) for reads and forms."""

import re
from urllib.parse import quote, urljoin, urlsplit

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
