"""A fake Hardshell edge served on localhost for exercising the client.

No network access beyond 127.0.0.1; no mocking of the client's internals —
requests go through the real transport stack.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
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
class _Response:
    status: int = 200
    body: Any = None
    raw_body: bytes | None = None
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0

    def encoded(self) -> bytes:
        if self.raw_body is not None:
            return self.raw_body
        return json.dumps({} if self.body is None else self.body).encode("utf-8")


@dataclass
class FakeEdge:
    server: ThreadingHTTPServer | None = None
    requests: list[RecordedRequest] = field(default_factory=list)
    responses: dict[tuple[str, str], list[_Response]] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def last(self) -> RecordedRequest:
        return self.requests[-1]

    def respond(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: Any = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Set the response for (method, path); default is 200 {}.

        ``raw_body`` overrides ``body`` with non-JSON bytes; ``delay`` makes
        the handler stall before responding (for timeout tests).
        """
        self.responses[(method, path)] = [
            _Response(
                status=status,
                body=body,
                raw_body=raw_body,
                content_type=content_type,
                headers=headers or {},
                delay=delay,
            )
        ]

    def respond_sequence(self, method: str, path: str, bodies: list) -> None:
        """Queue several 200 responses for (method, path), served in order;
        the final one repeats for any further requests."""
        self.responses[(method, path)] = [_Response(body=b) for b in bodies]


class _Handler(BaseHTTPRequestHandler):
    # Each FakeEdge gets its own _Handler subclass with `edge` bound to it,
    # so concurrent servers and lingering handler threads can't record into
    # another test's edge.
    edge: FakeEdge

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
        queued = self.edge.responses.get((method, split.path))
        if not queued:
            response = _Response()
        elif len(queued) > 1:
            response = queued.pop(0)
        else:
            response = queued[0]
        if response.delay:
            time.sleep(response.delay)
        encoded = response.encoded()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
        pass


@pytest.fixture
def make_edge() -> Iterator[Callable[[], FakeEdge]]:
    """Factory for independent fake edges; each gets its own server and
    handler class, so several can safely run at once."""
    servers: list[ThreadingHTTPServer] = []

    def factory() -> FakeEdge:
        fake = FakeEdge()
        handler = type("_BoundHandler", (_Handler,), {"edge": fake})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        fake.server = server
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return fake

    yield factory
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def edge(make_edge: Callable[[], FakeEdge]) -> FakeEdge:
    return make_edge()


@pytest.fixture
def client(edge: FakeEdge):
    from hardshell_telemetry import HardshellClient

    return HardshellClient(api_key="test-key", base_url=edge.base_url)
