---
name: hardshell-integration
description: Verify, plan, and register a RAG corpus with Hardshell using the `hardshell` CLI. Use when integrating hardshell-telemetry into an application, debugging why retrievals aren't joining to registered documents, or migrating an existing corpus. Triggers on "hardshell", "register corpus", "telemetry not joining", "smoke test the integration".
---

# Managing a Hardshell integration with the CLI

The `hardshell` CLI ships with `pip install hardshell-telemetry`. Every
network command reads `HARDSHELL_API_KEY` and `HARDSHELL_BASE_URL` from the
environment (flags `--api-key`/`--base-url` override). Add `--json` to any
command for machine-readable output — prefer it when parsing results.

**The one invariant behind every command:** the chunk id you register must
equal — byte for byte — the chunk id your retrieval path reports. Every
failure mode below is some violation of that.

## Which command, when

| Situation | Run |
|---|---|
| Fresh integration, first thing | `hardshell smoke-test` |
| Existing corpus, before registering anything | `hardshell validate-corpus corpus.jsonl` |
| About to switch id strategies or libraries | `hardshell register-corpus corpus.jsonl --dry-run --strategy <s>` |
| Ready to register | `hardshell register-corpus corpus.jsonl` |
| Check what Hardshell sees | `hardshell report document-access --days 7` |

## smoke-test

`hardshell smoke-test --json` registers a disposable document + two chunks,
records a retrieval against them, and polls the report until the join shows.
Traffic is labeled `source="testing"` — it never pollutes production
baselines. Exit 0 = the whole pipeline works.

Failure → fix map (step names from the JSON `steps` array):

| Failing step | Meaning | Fix |
|---|---|---|
| `config` | missing credentials | set `HARDSHELL_API_KEY` / `HARDSHELL_BASE_URL` |
| `register` with HTTP 401 | key rejected | wrong or revoked API key |
| `register` with connection error | endpoint unreachable | check `HARDSHELL_BASE_URL`; the client never follows redirects, so use the final endpoint |
| `register` with non-JSON response | URL points at something else (proxy page, wrong service) | fix `HARDSHELL_BASE_URL` |
| `join` (register/record passed) | report lag or id mismatch | rerun with `--poll-seconds 60`; if it persists, retrieval-path ids ≠ registered ids |

## Corpus file format

JSONL, one document per line:

```json
{"id": "kb/handbook.pdf", "name": "Handbook", "content": "full text (hashed locally, never sent)", "chunks": ["handbook:0001", {"id": "handbook:0002"}]}
```

`id` = the customer's stable document id (preferred). `content` without `id`
derives a content-addressed id (warns: edits fork identity). Chunk entries
are the ids **already in the vector store**, verbatim.

## Strategies (`--strategy`)

- `default` — ids pass through verbatim; minted only where absent. Start here.
- `existing` — everything verbatim; any record missing an id is an error. Use for brownfield migrations.
- `derived` — mint uniform opaque ids for everything. Only for brand-new indexes.
- `legacy:<uuid>` — reproduce a customer's own uuid5(namespace, id) scheme.

## register-corpus semantics (join-safety rails)

- `--dry-run` prints the id plan: what changes, what's minted, collisions,
  fork-on-edit warnings. Exit 0 = safe. **Always dry-run first on an existing
  corpus.**
- A real run **aborts before sending anything** if any record errors under
  the strategy, or if any existing id would change — id changes require the
  explicit `--allow-id-changes` flag (that's a migration decision, not a
  default).
- Registration is an upsert; re-running is safe.

## Interpreting `report document-access`

Zero retrievals against documents you know are being queried: rule out the
mundane causes first — rerun **without** `--days`/`--limit` (a retrieval
outside the window or a page cut by the limit also reads as zero) and allow
a minute of report lag. If a broad, unrestricted query still shows zero,
it's the id mismatch case: the application's retrieval path is reporting
different chunk ids than were registered. Compare a live retrieval's ids
against the registered ones (`--json` and diff).

## What this CLI does not do (yet)

Import ids directly from a vector store (`import-store`), diff against
registered state server-side, and join-health diagnostics are planned; today
the corpus file is the interchange. Raw document text is hashed locally and
never transmitted — only ids, hashes, names, and metadata leave the machine.
