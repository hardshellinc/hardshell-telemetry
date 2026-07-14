# hardshell-telemetry — agent guide

Python client library for the Hardshell telemetry API. Standard library only —
the base package must never gain runtime dependencies.

## Commands

```sh
uv sync              # install dev dependencies (pytest, ruff)
uv run pytest        # full test suite; fast, no network beyond 127.0.0.1
uv run ruff check .  # lint
uv run ruff format --check .  # formatting (if in doubt, match existing style)
uv build             # build wheel + sdist into dist/
```

Run tests and lint before committing. There is no app to launch — this is a
library; the closest thing to "running it" is the scripts in `examples/`
(they need real `HARDSHELL_API_KEY` / `HARDSHELL_BASE_URL` env vars and hit a
live endpoint, so don't run them in CI or tests).

## Layout

- `src/hardshell_telemetry/client.py` — `TelemetryClient`: transport, auth,
  one method per public endpoint.
- `src/hardshell_telemetry/types.py` — dataclasses mirroring the REST
  contract; inputs implement `to_payload()`, outputs `from_payload()`.
- `src/hardshell_telemetry/exceptions.py` — `TelemetryError`.
- `tests/conftest.py` — `FakeEdge`, a real localhost HTTP server used by all
  client tests; prefer it over mocking the transport.
- `examples/` — runnable, documented scripts; keep them in sync with the API.

## Conventions

- Every public method/field carries a docstring; the library is the product,
  docs are part of the contract.
- Typed inputs are conveniences, not gates: every ingest method also accepts
  plain dicts and sends them verbatim. Don't add client-side validation that
  would reject payloads the server accepts.
- Optional dataclass fields left as `None` are omitted from the wire payload
  (never sent as `null`).
- `base_url` has no default and must stay that way.
- New API surface needs: types + client method + tests against `FakeEdge` +
  README section + example coverage where it fits.
