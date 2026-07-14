"""A fake Hardshell edge served on localhost for exercising the client.

No network access beyond 127.0.0.1; no mocking of the client's internals —
requests go through the real transport stack.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    json: Any


@dataclass
class FakeEdge:
    server: ThreadingHTTPServer
    requests: list[RecordedRequest] = field(default_factory=list)
    responses: dict[tuple[str, str], tuple[int, Any]] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def last(self) -> RecordedRequest:
        return self.requests[-1]

    def respond(self, method: str, path: str, *, status: int = 200, body: Any = None) -> None:
        """Set the response for (method, path); default is 200 {}."""
        self.responses[(method, path)] = (status, {} if body is None else body)


class _Handler(BaseHTTPRequestHandler):
    edge: FakeEdge  # set by the fixture

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.edge.requests.append(
            RecordedRequest(
                method=method,
                path=split.path,
                query=parse_qs(split.query),
                headers=dict(self.headers.items()),
                json=json.loads(raw) if raw else None,
            )
        )
        status, body = self.edge.responses.get((method, split.path), (200, {}))
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
        pass


@pytest.fixture
def edge() -> Iterator[FakeEdge]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    fake = FakeEdge(server=server)
    _Handler.edge = fake
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(edge: FakeEdge):
    from hardshell_telemetry import HardshellClient

    return HardshellClient(api_key="test-key", base_url=edge.base_url)
