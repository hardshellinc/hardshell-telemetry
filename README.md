# hardshell-telemetry

Official [Hardshell](https://hardshell.ai) Python client for sending AI/RAG
retrieval telemetry and reading the security reports derived from it.

> **Status: name reservation.** Version `0.0.0` is a placeholder — the first
> functional release is coming soon. Don't install this yet.

## What this will be

A lightweight, dependency-free REST client you drop into your application to:

- **Emit telemetry** — retrieval spans (which chunks a query returned, scores,
  actor context) plus document and chunk metadata for enrichment.
- **Read reports** — pull the derived signals back out, starting with
  document-access reports.

## License

[Apache-2.0](LICENSE)
