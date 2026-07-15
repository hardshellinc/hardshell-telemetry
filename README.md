# hardshell-telemetry

Official [Hardshell](https://hardshell.ai) Python client for sending AI/RAG
retrieval telemetry and reading the security reports derived from it.

Dependency-free (standard library only), fully typed, Apache-2.0.

> **Status: pre-release.** The PyPI version is a placeholder. Until the first
> release, install from git:
>
> ```sh
> pip install git+https://github.com/hardshellinc/hardshell-telemetry
> ```

## What it does

Hardshell watches how your RAG application's corpus is actually being
retrieved and flags exfiltration-shaped behavior. This client is how your app
talks to it:

- **Register documents and chunks** (once per corpus build) so retrievals can
  be joined to metadata like sensitivity.
- **Record retrievals** (per query) — which chunks each search returned, and
  for whom.
- **Read reports** — pull derived signals back out, starting with
  document-access summaries.

The interactive API reference lives at `<your-endpoint>/docs`.

## Quickstart

```python
from hardshell_telemetry import HardshellClient

client = HardshellClient(
    api_key="hs-...",                            # from your Hardshell onboarding
    base_url="https://<your-hardshell-endpoint>",  # ditto
    source="production",                         # optional provenance label
)

# After each vector-store query — chunks as (chunk_id, score), scores in [0, 1]:
client.record_retrieval(
    chunks=[("doc-42:chunk-3", 0.91), ("doc-42:chunk-7", 0.88)],
    user_id="end-user-123",
    backend="chroma",
)
```

Your organization is derived from the API key server-side — you never send a
tenant or org id. The client never follows redirects, so `base_url` must be
your final Hardshell endpoint.

### Make it non-fatal in production

Failed requests raise `TelemetryError` by default so setup problems are
visible during integration. In production, wrap the call so a network blip
never breaks a user's retrieval:

```python
try:
    client.record_retrieval(...)
except Exception:
    logging.warning("hardshell telemetry failed (non-fatal)", exc_info=True)
```

### Labeling traffic with `source`

`source` says *where traffic came from* — free-form, commonly `"production"`,
`"staging"`, `"testing"`, `"simulation"`, `"evaluation"`. Hardshell uses it to
keep experimental traffic out of production detection baselines. Resolution
order: per-call `source` → client default → your API key's environment
default (server-side) → unlabeled. If Hardshell issued you an
environment-scoped key, leave `source` unset and the key decides; pass
`source=""` to force a payload through unlabeled.

### Batching spans

If you'd rather not send per query, build `RetrievalSpan` objects as
retrievals happen and flush them in one call. Each span's timestamp is
captured when the span is constructed, so batched events keep their real
event times:

```python
from hardshell_telemetry import RetrievalSpan

pending.append(RetrievalSpan(chunks=[("c-1", 0.91)], backend="chroma", user_id="u-1"))
# ... later, from your flush loop:
client.ingest_spans(pending)
```

## Registering your corpus

Retrievals are joined to document/chunk metadata by id, so register your
corpus once per index build. **The chunk ids you register must be the same
ids your retrieval path reports** — that id is the join key.

```python
from hardshell_telemetry import Chunk, Document, DocumentLink

client.ingest_documents([
    Document(
        document_id="employee-handbook",
        name="Employee Handbook (2026)",
        sensitivity=0.4,                    # your own 0–1 scale
        sensitivity_level="internal",       # your own labels
        custom_metadata={"owner": "people-ops"},
    ),
])

client.ingest_chunks([
    Chunk(
        chunk_id="employee-handbook:0001",
        sensitivity_level="internal",
        document_links=[DocumentLink(document_id="employee-handbook")],
    ),
])
```

All ingest methods also accept plain dicts, passed through verbatim, if you'd
rather build payloads yourself.

## Reading reports

```python
from datetime import datetime, timedelta, timezone

report = client.document_access_report(
    window_start=datetime.now(timezone.utc) - timedelta(days=7),
    limit=20,
)
for doc in report.documents:
    print(doc.document_id, sum(c.access_count for c in doc.chunks))
```

## Examples

Runnable scripts live in [`examples/`](examples/): corpus registration,
retrieval recording, and report reading. Each takes `HARDSHELL_API_KEY` and
`HARDSHELL_BASE_URL` from the environment.

## Roadmap

- **Document intake helpers** — opinionated utilities for deriving stable
  document ids, content hashing, and sensitivity recording (next up).
- **Framework instrumentation** — auto-instrumentation for common RAG stacks,
  as optional extras; the base install stays dependency-free.

## Development

```sh
uv sync          # install with dev dependencies
uv run pytest    # tests run against a local fake server; no network
uv run ruff check . && uv run ruff format --check .
uv run ty check  # type-check
uv build         # wheel + sdist
```

## License

[Apache-2.0](LICENSE)
