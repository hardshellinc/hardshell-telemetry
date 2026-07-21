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
- **Group by corpus** — tag each vector store you retrieve from to see how a
  single document is accessed across every index it lives in.

The interactive API reference lives at `https://api.qa.hardshell.dev/docs`.
Note that this is currently in development, and the endpoint is a QA endpoint!
This endpoint will be updated when the library is published.  If you are interested
in being a beta user, please reach out to ben@hardshell.ai.

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

## Registering documents & chunks

Retrievals are joined to document/chunk metadata by id, so register your
documents and chunks once per index build. **The chunk ids you register must
be the same ids your retrieval path reports** — that id is the join key.

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

### Deriving document ids

No stable document id? Derive one — deterministically, so re-ingesting
upserts instead of forking, and anyone holding the same inputs re-derives the
same id:

```python
from hardshell_telemetry import content_hash, derive_document_id

doc_id = derive_document_id("s3://corpus/handbook.pdf")      # from your natural key
doc_id = derive_document_id(content=raw_text)                # or from the content itself
# recommended: both — stable identity plus change detection
doc_id = derive_document_id("s3://corpus/handbook.pdf", content=raw_text)
client.ingest_documents([{"document_id": doc_id, "content_hash": content_hash(raw_text)}])
```

Content is hashed locally and never transmitted. **Chunk ids are different**:
if your vector store already has chunk ids, register those verbatim — the id
your retrieval path reports is the join key. `derive_chunk_id` exists only
for brand-new indexes. `sensitivity_from_level("high")` maps ordered tier
labels onto the 0–1 `sensitivity` scale (custom vocabularies via `scale=`).

### Chunking

Built-in chunk strategies, or bring anything with `chunk(text) -> list[str]`:

```python
from hardshell_telemetry import FixedSizeChunker, ParagraphChunker, SentenceChunker

chunks = FixedSizeChunker(800, overlap=100).chunk(text)
chunks = ParagraphChunker(max_chars=1200).chunk(text)   # packs paragraphs, never cuts inside one
```

## Corpora — one document across many indexes

A **corpus** is a vector store you retrieve from, named `backend:collection`
(e.g. `qdrant:docs-prod`, `pgvector:kb`). Tag your writes and your retrievals
with it and Hardshell shows **how a single document is retrieved across every
index it lives in** — the view that catches one document leaking through a
store you forgot to lock down.

`corpus` works exactly like [`source`](#labeling-traffic-with-source): set it
once on the client, override per call, or omit it — same resolution order.

```python
from hardshell_telemetry import Chunk, Document, DocumentLink, HardshellClient, corpus_name

client = HardshellClient(
    api_key="hs-...",
    base_url="https://<your-hardshell-endpoint>",
    corpus=corpus_name("qdrant", "docs-prod"),   # default for everything this client sends
)

# index-build time — register the handbook into this corpus
client.ingest_documents([Document(document_id="employee-handbook")])
client.ingest_chunks([
    Chunk(chunk_id="employee-handbook:0001",
          document_links=[DocumentLink(document_id="employee-handbook")]),
])

# query time — the retrieval inherits the client's corpus, nothing extra to pass
client.record_retrieval(chunks=[("employee-handbook:0001", 0.92)], backend="qdrant")
```

Indexing the same document into a second store? A document can belong to many
corpora — override per call:

```python
client.ingest_documents([Document(document_id="employee-handbook")],
                        corpus="pgvector:kb")
client.record_retrieval(chunks=[("employee-handbook:0001", 0.88)],
                       backend="pgvector", corpus="pgvector:kb")
```

**Naming.** Use `backend:collection`, lowercase and stable — `corpus_name()`
builds it for you. A bare collection name collides across stores, and a name
that drifts by a stray capital or space is a *different* corpus, which splits
your reports. (Our `Retriever`/`Ingestor` fronts derive the name from your
store handle automatically; at this layer you set it.)

### Seeing it back

The document-access report breaks each document's retrievals down by the
corpus they read from — unlabeled traffic buckets under `""`:

```python
report = client.document_access_report(limit=20)
for doc in report.documents:
    for c in doc.corpora:
        print(f"{doc.document_id} · {c.corpus or '(unlabeled)'}: {c.access_count}")

# employee-handbook · qdrant:docs-prod: 141
# employee-handbook · pgvector:kb: 3      ← same doc, served from a second index
```

`doc.corpora` sums to the same total as `doc.chunks` — the same accesses,
sliced by index instead of by chunk.

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

## The `hardshell` CLI

Installed with the package. Set `HARDSHELL_API_KEY` and `HARDSHELL_BASE_URL`,
then:

```sh
hardshell smoke-test                              # end-to-end: register → record → join
hardshell validate-corpus corpus.jsonl            # local join-safety checks, no network
hardshell register-corpus corpus.jsonl --dry-run  # "these ids will change" report
hardshell register-corpus corpus.jsonl            # actually register (aborts on id changes
                                                  #   unless --allow-id-changes)
hardshell report document-access --days 7         # what Hardshell sees
```

Corpus files are JSONL, one document per line —
`{"id": ..., "name": ..., "content": ..., "chunks": ["chunk-id", ...]}` —
with document content hashed locally and never transmitted. Add `--json` to
any command for machine-readable output. Integrating with a coding agent?
Point it at
[`.claude/skills/hardshell-integration/SKILL.md`](.claude/skills/hardshell-integration/SKILL.md).

## Examples

Runnable scripts live in [`examples/`](examples/): corpus registration,
retrieval recording, and report reading. Each takes `HARDSHELL_API_KEY` and
`HARDSHELL_BASE_URL` from the environment.

## Roadmap

- **Vector-store integration** — pick your vector DB (pgvector and Qdrant
  first, as optional extras) and use our ingest/retrieval fronts; ids,
  registration, and span recording handled for you (next up).
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

## Appendix: how we chose document identity

Design rationale for the id system, informed by a survey of how RAG
frameworks and production teams handle identity (July 2026). The short
version: **your id is the identity; the content hash is the version; we never
transform an id you already have.**

**Two-tier identity.** A document's id is a stable pointer; its
`content_hash` records what the content was when registered. This mirrors
git (ref → blob) and is the consensus of every production writeup we
studied — the alternative, using the content hash *as* the id (LangChain's
indexing API, Haystack), means every edit re-keys the document, which is why
those systems need bookkeeping tables and delete-and-re-add cleanup modes to
function at all.

**Ids pass through verbatim.** Retrieval telemetry joins on the exact id
string your retrieval path reports. Any transformation applied at
registration but not at query time silently breaks that join — so the
library never wraps, hashes, or normalizes an id you supply. Derivation
(`derive_document_id`, `derive_chunk_id`) exists only for records that have
no id yet, and it's deterministic (uuid5 under a pinned, published
namespace) so anyone can re-derive the same id from the same inputs, in any
language, forever.

**Metadata never goes in the hash.** Frameworks that hash content + metadata
into identity have repeatedly shipped identity breakage: unstable dict
serialization changed every id (Haystack ≤2.x → 3.0), and a hash-recipe
regression made metadata edits invisible (LlamaIndex 0.12). Including
metadata means any bookkeeping key forks identity; our `content_hash` is
content-only, so metadata edits never fork a document and content edits are
visible as version changes.

**Re-chunking creates generations, not deletions.** Chunk identity does not
survive a re-chunk anywhere in the ecosystem — the standard coping
strategies are "never expose chunk ids" or "freeze your chunking
parameters." We keep it honest instead: new chunking runs register new chunk
ids alongside the old ones, all linked to the same document, so lineage is
preserved and stale-index retrievals remain attributable rather than
becoming unknowns.

**Migration is a diff, not a leap of faith.** Across LangChain, LlamaIndex,
and Haystack, changing an id scheme means "start a fresh index." Here,
`plan_ids` reports exactly which ids would change — locally, before anything
is sent — so switching libraries or strategies is a reviewed decision
instead of a silent join break.

## License

[Apache-2.0](LICENSE)
