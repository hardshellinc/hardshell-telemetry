"""The ``hardshell`` command-line interface.

Installed as a console script with the package — no extra dependencies.
Configuration comes from ``HARDSHELL_API_KEY`` / ``HARDSHELL_BASE_URL``
(overridable with ``--api-key`` / ``--base-url``). Every network-facing
subcommand supports ``--json`` for machine-readable output; exit codes are
meaningful (0 = ok/safe, 1 = failed/unsafe, 2 = usage/config error).

Subcommands:

- ``smoke-test``       — end-to-end verify: register → record → report join.
- ``validate-corpus``  — local join-safety checks over a JSONL corpus.
- ``register-corpus``  — plan (``--dry-run``) or register a JSONL corpus.
- ``report``           — read reports back (``document-access``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hardshell_telemetry._version import __version__
from hardshell_telemetry.client import HardshellClient
from hardshell_telemetry.exceptions import TelemetryError
from hardshell_telemetry.ids import (
    ChunkRecord,
    DefaultIds,
    DerivedIds,
    DocumentRecord,
    ExistingIds,
    IdPlan,
    IdStrategy,
    LegacyNamespaceIds,
    plan_ids,
)
from hardshell_telemetry.types import Chunk, Document, DocumentAccessSummary, DocumentLink

__all__ = ["main"]

_CONFIG_HINT = (
    "set HARDSHELL_API_KEY and HARDSHELL_BASE_URL (from your Hardshell "
    "onboarding), or pass --api-key/--base-url"
)


class _CliError(Exception):
    """Fatal usage/config error → exit code 2, message on stderr."""


def _client(args: argparse.Namespace) -> HardshellClient:
    api_key = args.api_key or os.environ.get("HARDSHELL_API_KEY")
    base_url = args.base_url or os.environ.get("HARDSHELL_BASE_URL")
    if not api_key or not base_url:
        raise _CliError(f"missing configuration — {_CONFIG_HINT}")
    return HardshellClient(api_key=api_key, base_url=base_url, source=args.source)


def _add_connection_args(parser: argparse.ArgumentParser, *, default_source: str | None) -> None:
    parser.add_argument("--api-key", help="Hardshell API key (default: $HARDSHELL_API_KEY)")
    parser.add_argument("--base-url", help="Hardshell endpoint (default: $HARDSHELL_BASE_URL)")
    parser.add_argument(
        "--source",
        default=default_source,
        help=f"provenance label for traffic sent by this command (default: {default_source})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number, got {value}")
    return number


def _hint_for(error: TelemetryError) -> str:
    if error.status_code == 401:
        return "the API key was rejected — check HARDSHELL_API_KEY"
    if error.status_code == 404:
        return "endpoint not found — is base_url pointing at your Hardshell endpoint?"
    if error.status_code is None:
        return "could not reach the endpoint — check HARDSHELL_BASE_URL and your network"
    return "see the response detail above"


# ── smoke-test ───────────────────────────────────────────────────────────────


@dataclass
class _Step:
    name: str
    ok: bool
    detail: str


def _cmd_smoke_test(args: argparse.Namespace) -> int:
    steps: list[_Step] = []
    run_id = args.run_id or uuid.uuid4().hex[:8]
    doc_id = f"hardshell-smoke:{run_id}"
    chunk_ids = [f"hardshell-smoke:{run_id}:0", f"hardshell-smoke:{run_id}:1"]

    def emit(step: _Step) -> None:
        steps.append(step)
        if not args.json:
            mark = "PASS" if step.ok else "FAIL"
            print(f"[{mark}] {step.name}: {step.detail}")

    def finish() -> int:
        passed = all(s.ok for s in steps)
        if args.json:
            print(
                json.dumps(
                    {
                        "passed": passed,
                        "steps": [vars(s) for s in steps],
                        "document_id": doc_id,
                        "chunk_ids": chunk_ids,
                    }
                )
            )
        else:
            print("smoke test PASSED" if passed else "smoke test FAILED")
        return 0 if passed else 1

    try:
        client = _client(args)
    except _CliError:
        raise
    emit(_Step("config", True, "api key and base_url present"))

    try:
        client.ingest_documents(
            [Document(document_id=doc_id, name=f"Hardshell smoke test {run_id}")]
        )
        client.ingest_chunks(
            [
                Chunk(chunk_id=cid, document_links=[DocumentLink(document_id=doc_id)])
                for cid in chunk_ids
            ]
        )
        emit(_Step("register", True, f"document + {len(chunk_ids)} chunks upserted"))
    except TelemetryError as exc:
        emit(_Step("register", False, f"{exc} — {_hint_for(exc)}"))
        return finish()

    try:
        spans = client.record_retrieval(
            chunks=[(chunk_ids[0], 0.91), (chunk_ids[1], 0.88)],
            user_id=f"hardshell-smoke-{run_id}",
            backend="smoke-test",
        )
        emit(_Step("record", True, f"{spans.spans_accepted} span accepted"))
    except TelemetryError as exc:
        emit(_Step("record", False, f"{exc} — {_hint_for(exc)}"))
        return finish()

    def find_document() -> DocumentAccessSummary | None:
        # The report is paginated; walk every page — on a busy tenant the
        # smoke document may not be on the first one.
        page_size, offset = 200, 0
        while True:
            report = client.document_access_report(limit=page_size, offset=offset)
            doc = next((d for d in report.documents if d.document_id == doc_id), None)
            if doc:
                return doc
            offset += len(report.documents)
            if not report.documents or offset >= report.total_documents:
                return None

    deadline = time.monotonic() + args.poll_seconds
    joined = False
    while time.monotonic() < deadline:
        try:
            doc = find_document()
        except TelemetryError as exc:
            emit(_Step("report", False, f"{exc} — {_hint_for(exc)}"))
            return finish()
        if doc and {c.chunk_id for c in doc.chunks if c.access_count} >= set(chunk_ids):
            joined = True
            break
        time.sleep(min(2.0, args.poll_seconds / 5))
    if joined:
        emit(_Step("join", True, "retrieval joined back to the registered chunks"))
    else:
        emit(
            _Step(
                "join",
                False,
                f"registered chunk ids never showed retrievals within {args.poll_seconds}s — "
                "if register and record passed, the ids your retrieval path reports may "
                "not match the ids you register",
            )
        )
    return finish()


# ── corpus loading (JSONL) ───────────────────────────────────────────────────


def _load_corpus(path: str) -> list[DocumentRecord]:
    """JSONL, one document per line:
    {"id": ..., "content": ..., "name": ..., "chunks": ["c-1", {"id": ..., "content": ...}]}
    """
    records: list[DocumentRecord] = []
    file = Path(path)
    if not file.exists():
        raise _CliError(f"corpus file not found: {path}")
    for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _CliError(f"{path}:{line_no}: not valid JSON — {exc}") from exc
        if not isinstance(raw, dict):
            raise _CliError(f"{path}:{line_no}: expected a JSON object per line")
        raw_chunks = raw.get("chunks", [])
        if not isinstance(raw_chunks, list):
            raise _CliError(f"{path}:{line_no}: chunks must be a JSON array of ids or objects")
        chunks: list[ChunkRecord | str] = []
        for position, chunk in enumerate(raw_chunks):
            if isinstance(chunk, str):
                chunks.append(chunk)
            elif isinstance(chunk, dict):
                existing_id = chunk.get("id")
                chunk_content = chunk.get("content")
                if existing_id is not None and not isinstance(existing_id, str):
                    raise _CliError(f"{path}:{line_no}: chunk {position} id must be a string")
                if chunk_content is not None and not isinstance(chunk_content, str):
                    raise _CliError(f"{path}:{line_no}: chunk {position} content must be a string")
                chunks.append(ChunkRecord(existing_id=existing_id, content=chunk_content))
            else:
                raise _CliError(
                    f"{path}:{line_no}: chunk {position} must be a string id or an object"
                )
        records.append(
            DocumentRecord(
                provided_id=raw.get("id"),
                content=raw.get("content"),
                name=raw.get("name"),
                chunks=chunks,
            )
        )
    if not records:
        raise _CliError(f"{path}: no documents found (JSONL, one document object per line)")
    return records


def _parse_strategy(spec: str) -> IdStrategy:
    if spec == "default":
        return DefaultIds()
    if spec == "existing":
        return ExistingIds()
    if spec == "derived":
        return DerivedIds()
    if spec.startswith("legacy:"):
        try:
            return LegacyNamespaceIds(uuid.UUID(spec.removeprefix("legacy:")))
        except ValueError as exc:
            raise _CliError(f"legacy strategy needs a UUID namespace, got {spec!r}") from exc
    raise _CliError(f"unknown strategy {spec!r} — use default, existing, derived, or legacy:<uuid>")


def _print_plan(plan: IdPlan, as_json: bool) -> None:
    if as_json:
        payload = {k: v for k, v in vars(plan).items()}
        payload["safe"] = plan.safe
        print(json.dumps(payload))
    else:
        print(plan.summary())


# ── validate-corpus / register-corpus ────────────────────────────────────────


def _cmd_validate_corpus(args: argparse.Namespace) -> int:
    records = _load_corpus(args.corpus)
    plan = plan_ids(records, _parse_strategy(args.strategy))
    _print_plan(plan, args.json)
    if plan.errors:
        return 1
    if args.strict and plan.warnings:
        return 1
    return 0


def _cmd_register_corpus(args: argparse.Namespace) -> int:
    records = _load_corpus(args.corpus)
    strategy = _parse_strategy(args.strategy)
    plan = plan_ids(records, strategy)
    _print_plan(plan, args.json)

    if args.dry_run:
        return 0 if plan.safe else 1
    if plan.errors:
        print(
            f"aborting: {len(plan.errors)} record(s) failed under this strategy — nothing was sent",
            file=sys.stderr,
        )
        return 1
    changes = plan.document_changes_total + plan.chunk_changes_total
    if changes and not args.allow_id_changes:
        print(
            f"aborting: {changes} id(s) would change under strategy {args.strategy!r} — "
            "data recorded under the current ids would no longer join. "
            "Re-run with --allow-id-changes if this migration is intentional",
            file=sys.stderr,
        )
        return 1

    client = _client(args)
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for record in records:
        doc_id = strategy.document_id(provided_id=record.provided_id, content=record.content)
        documents.append(Document(document_id=doc_id, name=record.name))
        for index, chunk in enumerate(record.chunks):
            if isinstance(chunk, str):
                chunk = ChunkRecord(existing_id=chunk)
            chunks.append(
                Chunk(
                    chunk_id=strategy.chunk_id(
                        document_id=doc_id,
                        index=index,
                        existing_id=chunk.existing_id,
                        content=chunk.content,
                    ),
                    document_links=[DocumentLink(document_id=doc_id)],
                )
            )

    try:
        doc_result = client.ingest_documents(documents)
        registered_chunks = 0
        for start in range(0, len(chunks), args.batch_size):
            registered_chunks += client.ingest_chunks(
                chunks[start : start + args.batch_size]
            ).chunks_upserted
    except TelemetryError as exc:
        print(f"registration failed: {exc} — {_hint_for(exc)}", file=sys.stderr)
        return 1

    summary = {
        "documents_registered": doc_result.documents_upserted,
        "chunks_registered": registered_chunks,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"registered {summary['documents_registered']} documents, "
            f"{summary['chunks_registered']} chunks"
        )
    return 0


# ── report ───────────────────────────────────────────────────────────────────


def _cmd_report(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta

    client = _client(args)
    window_start = datetime.now(UTC) - timedelta(days=args.days) if args.days is not None else None
    try:
        report = client.document_access_report(window_start=window_start, limit=args.limit or None)
    except TelemetryError as exc:
        print(f"report failed: {exc} — {_hint_for(exc)}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "total_documents": report.total_documents,
                    "documents": [
                        {
                            "document_id": d.document_id,
                            "name": d.name,
                            "chunk_count": d.chunk_count,
                            "accesses": sum(c.access_count for c in d.chunks),
                            "chunks": [
                                {"chunk_id": c.chunk_id, "access_count": c.access_count}
                                for c in d.chunks
                            ],
                        }
                        for d in report.documents
                    ],
                }
            )
        )
        return 0

    print(f"{report.total_documents} documents registered")
    for doc in report.documents:
        total = sum(c.access_count for c in doc.chunks)
        label = doc.name or doc.document_id
        print(f"  {label}: {total} retrievals across {doc.chunk_count} chunks")
    return 0


# ── entry point ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hardshell",
        description="Hardshell telemetry CLI — verify, plan, and register RAG corpora.",
    )
    parser.add_argument("--version", action="version", version=f"hardshell-telemetry {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser(
        "smoke-test", help="end-to-end verify: register, record, and join a test document"
    )
    _add_connection_args(smoke, default_source="testing")
    smoke.add_argument(
        "--poll-seconds",
        type=_positive_float,
        default=30.0,
        help="how long to wait for the report join (default 30)",
    )
    smoke.add_argument("--run-id", help="fixed run id (for reproducible test runs)")
    smoke.set_defaults(handler=_cmd_smoke_test)

    validate = commands.add_parser(
        "validate-corpus", help="local join-safety checks over a JSONL corpus (no network)"
    )
    validate.add_argument("corpus", help="JSONL file, one document object per line")
    validate.add_argument("--strategy", default="default")
    validate.add_argument("--strict", action="store_true", help="treat warnings as failures too")
    validate.add_argument("--json", action="store_true", help="machine-readable output")
    validate.set_defaults(handler=_cmd_validate_corpus)

    register = commands.add_parser(
        "register-corpus", help="plan (--dry-run) or register a JSONL corpus"
    )
    register.add_argument("corpus", help="JSONL file, one document object per line")
    register.add_argument("--strategy", default="default")
    register.add_argument(
        "--dry-run", action="store_true", help="print the id plan and exit; send nothing"
    )
    register.add_argument(
        "--allow-id-changes",
        action="store_true",
        help="proceed even when the strategy changes existing ids (an intentional migration)",
    )
    register.add_argument("--batch-size", type=_positive_int, default=500)
    _add_connection_args(register, default_source="index-build")
    register.set_defaults(handler=_cmd_register_corpus)

    report = commands.add_parser("report", help="read reports back from Hardshell")
    report_kind = report.add_subparsers(dest="kind", required=True)
    document_access = report_kind.add_parser(
        "document-access", help="how often your chunks are retrieved, by document"
    )
    document_access.add_argument("--days", type=_positive_int, help="window: last N days")
    document_access.add_argument("--limit", type=_positive_int, help="page size")
    _add_connection_args(document_access, default_source=None)
    document_access.set_defaults(handler=_cmd_report)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``hardshell`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = args.handler
    try:
        return handler(args)
    except _CliError as exc:
        print(f"hardshell: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
