"""Record and replay HTTP traffic so tests and CI never touch the network.

A cassette is a directory holding ``index.json`` (URL -> status, headers, body file) and
gzip-compressed bodies. ``RecordingTransport`` wraps a real transport and writes each
response; ``ReplayTransport`` serves them back and fails loudly on any unrecorded URL.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

KEPT_HEADERS = ("content-type", "etag", "last-modified", "retry-after")


class CassetteMissError(LookupError):
    """A replayed request has no recording."""


def body_name(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".gz"


def _load_index(root: Path) -> dict[str, dict[str, Any]]:
    index_path = root / "index.json"
    if not index_path.exists():
        return {}
    data: dict[str, dict[str, Any]] = json.loads(index_path.read_text(encoding="utf-8"))
    return data


class ReplayTransport(httpx.BaseTransport):
    """Serve recorded responses by exact URL."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = _load_index(root)
        if not self.index:
            raise CassetteMissError(f"no recordings found in {root}")
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        entry = self.index.get(url)
        if entry is None:
            raise CassetteMissError(
                f"no recording for {url}; re-record with scripts/record_fixtures.py"
            )
        body = gzip.decompress((self.root / entry["body"]).read_bytes())
        return httpx.Response(
            status_code=int(entry["status"]),
            headers=entry.get("headers", {}),
            content=body,
            request=request,
        )


class RecordingTransport(httpx.BaseTransport):
    """Pass requests through to ``inner`` and store every response."""

    def __init__(self, root: Path, inner: httpx.BaseTransport | None = None) -> None:
        self.root = root
        self.inner = inner or httpx.HTTPTransport()
        self.index = _load_index(root)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        body = response.read()
        url = str(request.url)
        name = body_name(url)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_bytes(gzip.compress(body, mtime=0))
        self.index[url] = {
            "status": response.status_code,
            "headers": {k: response.headers[k] for k in KEPT_HEADERS if k in response.headers},
            "body": name,
        }
        (self.root / "index.json").write_text(
            json.dumps(self.index, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        # The body is already decoded, so drop headers that describe the wire encoding.
        passthrough = [
            (k, v)
            for k, v in response.headers.items()
            if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        ]
        return httpx.Response(
            status_code=response.status_code,
            headers=passthrough,
            content=body,
            request=request,
        )

    def close(self) -> None:
        self.inner.close()
