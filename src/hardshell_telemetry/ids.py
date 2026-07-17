"""Identity strategies and the migration dry-run.

An :class:`IdStrategy` decides how document and chunk ids are produced at
ingest time. The rules it never breaks:

- **Your ids pass through verbatim when you have them.** The id your
  retrieval path reports is the join key; transforming it breaks the join.
- **No silent fallbacks.** A strategy that needs an id and doesn't get one
  raises with the record named — a corpus that's 98% existing ids and 2%
  quietly minted ones produces partial joins that look like detection noise.
- **Identity and version are different things.** A document's id is stable;
  its ``content_hash`` is the version. Content edits never fork identity
  unless identity was derived from content (the dry-run warns about those).

Use :func:`plan_ids` before switching strategies or libraries: it reports
which ids would change — locally, before anything is sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from hardshell_telemetry.intake import content_hash, derive_chunk_id, derive_document_id

__all__ = [
    "DEFAULT_ID_STRATEGY",
    "ChunkRecord",
    "DefaultIds",
    "DerivedIds",
    "DocumentRecord",
    "ExistingIds",
    "IdPlan",
    "IdStrategy",
    "LegacyNamespaceIds",
    "plan_ids",
]


@runtime_checkable
class IdStrategy(Protocol):
    """How document and chunk ids are produced at ingest time."""

    def document_id(
        self,
        *,
        provided_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        """Return the document id to register. Raise ValueError (naming the
        problem and the fix) rather than guessing."""
        ...

    def chunk_id(
        self,
        *,
        document_id: str,
        index: int,
        existing_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        """Return the chunk id to register. Raise ValueError rather than
        guessing."""
        ...


@dataclass(frozen=True)
class DefaultIds:
    """The recommended strategy: **verbatim when provided, minted only when
    absent**.

    Documents: your id passes through untransformed; with no id, a
    deterministic uuid5 is derived from the content (the dry-run flags those
    documents as fork-on-edit). Chunks: your existing id passes through;
    chunks produced by our chunker get :func:`derive_chunk_id` ids.
    """

    def document_id(
        self,
        *,
        provided_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if provided_id:
            return provided_id
        if content is not None:
            return derive_document_id(content=content)
        raise ValueError(
            "DefaultIds needs a document id or the document content — "
            "pass your system's id (preferred: identity survives edits), "
            "or content to derive a content-addressed id"
        )

    def chunk_id(
        self,
        *,
        document_id: str,
        index: int,
        existing_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if existing_id:
            return existing_id
        return derive_chunk_id(document_id, index, content=content)


@dataclass(frozen=True)
class ExistingIds:
    """Brownfield: **both ids verbatim, always** — you already have ids and
    nothing may be minted.

    A record without an id is a loud error, not a silent mint: mixed id
    schemes in one corpus produce partial joins that look like detection
    noise. Run :func:`plan_ids` first to find the gaps.
    """

    def document_id(
        self,
        *,
        provided_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if not provided_id:
            raise ValueError(
                "ExistingIds requires a document id and this record has none — "
                "provide your system's id, or use DefaultIds() to mint ids for "
                "records that lack one"
            )
        return provided_id

    def chunk_id(
        self,
        *,
        document_id: str,
        index: int,
        existing_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if not existing_id:
            raise ValueError(
                f"ExistingIds requires a chunk id and chunk {index} of document "
                f"{document_id!r} has none — pass the id your vector store uses "
                "(the id your retrieval path reports is the join key)"
            )
        return existing_id


@dataclass(frozen=True)
class DerivedIds:
    """Greenfield: **both ids minted** deterministically under the Hardshell
    namespace — uniform, opaque ids regardless of what the records carry.

    Documents: ``uuid5(HARDSHELL_DOC_NAMESPACE, provided_id or sha256(content))``
    — a provided id is still used as the *natural key*, so identity remains
    stable across content edits. Chunks: :func:`derive_chunk_id` format,
    configurable via ``separator`` / ``index_width`` / ``content_hash_chars``.

    Only choose this for a new index: the minted ids must be the ids stored
    in your vector store, or retrieval will report ids we never registered.
    """

    separator: str = ":"
    index_width: int = 4
    content_hash_chars: int = 8

    def document_id(
        self,
        *,
        provided_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        return derive_document_id(provided_id, content=content)

    def chunk_id(
        self,
        *,
        document_id: str,
        index: int,
        existing_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        return derive_chunk_id(
            document_id,
            index,
            content=content,
            separator=self.separator,
            index_width=self.index_width,
            content_hash_chars=self.content_hash_chars,
        )


@dataclass(frozen=True)
class LegacyNamespaceIds:
    """For teams that already uuid5 their ids under their own namespace:
    reproduce **their** scheme — ``uuid5(your_namespace, your_id)`` — so the
    ids we register equal the ids already in their store.

    Requires ids on every record (like :class:`ExistingIds`); the namespace
    transform is applied to documents and chunks alike.
    """

    namespace: uuid.UUID | str

    @property
    def _ns(self) -> uuid.UUID:
        return (
            self.namespace if isinstance(self.namespace, uuid.UUID) else uuid.UUID(self.namespace)
        )

    def document_id(
        self,
        *,
        provided_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if provided_id:
            return str(uuid.uuid5(self._ns, provided_id))
        if content is not None:
            return str(uuid.uuid5(self._ns, content_hash(content)))
        raise ValueError(
            "LegacyNamespaceIds needs a document id (or content) to transform — "
            "this record has neither"
        )

    def chunk_id(
        self,
        *,
        document_id: str,
        index: int,
        existing_id: str | None = None,
        content: str | bytes | None = None,
    ) -> str:
        if not existing_id:
            raise ValueError(
                f"LegacyNamespaceIds requires a chunk id to transform and chunk "
                f"{index} of document {document_id!r} has none"
            )
        return str(uuid.uuid5(self._ns, existing_id))


DEFAULT_ID_STRATEGY: IdStrategy = DefaultIds()


# ── Migration dry-run ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChunkRecord:
    """One chunk as your system holds it, for :func:`plan_ids`."""

    existing_id: str | None = None
    content: str | bytes | None = None


@dataclass(frozen=True)
class DocumentRecord:
    """One document as your system holds it, for :func:`plan_ids`.

    ``chunks`` entries may be :class:`ChunkRecord`s or bare id strings.
    """

    provided_id: str | None = None
    content: str | bytes | None = None
    name: str | None = None
    chunks: Sequence[ChunkRecord | str] = ()


@dataclass
class IdPlan:
    """What a strategy would do to a corpus's ids — nothing is sent.

    ``safe`` means: every id the strategy produces equals the id the record
    already carries, and no record errored. Anything else deserves a look
    before registering: changed ids break joins to previously registered
    data, and errors mean the strategy refused records.
    """

    documents_total: int = 0
    chunks_total: int = 0
    document_changes_total: int = 0
    chunk_changes_total: int = 0
    minted_documents_total: int = 0
    minted_chunks_total: int = 0
    document_change_samples: list[tuple[str, str]] = field(default_factory=list)
    chunk_change_samples: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        """True when no existing id changes and no record errored."""
        return not (self.document_changes_total or self.chunk_changes_total or self.errors)

    def summary(self) -> str:
        """Human-readable report (what the CLI prints)."""
        lines = [
            f"documents: {self.documents_total} "
            f"({self.document_changes_total} ids would change, "
            f"{self.minted_documents_total} newly minted)",
            f"chunks: {self.chunks_total} "
            f"({self.chunk_changes_total} ids would change, "
            f"{self.minted_chunks_total} newly minted)",
        ]
        for current, planned in self.document_change_samples:
            lines.append(f"  document: {current} -> {planned}")
        for current, planned in self.chunk_change_samples:
            lines.append(f"  chunk: {current} -> {planned}")
        lines.extend(f"WARNING: {w}" for w in self.warnings)
        lines.extend(f"ERROR: {e}" for e in self.errors)
        if self.safe:
            lines.append("SAFE: no existing ids change under this strategy.")
        else:
            lines.append(
                "IDS WILL CHANGE OR RECORDS ERRORED: registering under this "
                "strategy will not join with data recorded under the current ids."
            )
        return "\n".join(lines)


def plan_ids(
    records: Iterable[DocumentRecord],
    strategy: IdStrategy = DEFAULT_ID_STRATEGY,
    *,
    sample_limit: int = 10,
) -> IdPlan:
    """Dry-run a strategy over a corpus: report which ids would change,
    which would be minted, and which records the strategy refuses — locally,
    before anything is sent.

    Warnings cover the join-safety hazards: two documents deriving the same
    id (collision — e.g. duplicate names used as keys), and documents whose
    identity comes from content alone (a content edit forks them into a new
    identity; give those a stable id).
    """
    plan = IdPlan()
    seen_documents: dict[str, int] = {}
    seen_chunks: dict[str, tuple[int, int]] = {}

    for position, record in enumerate(records):
        plan.documents_total += 1
        ref = record.provided_id or record.name or f"record #{position}"

        try:
            planned_doc = strategy.document_id(
                provided_id=record.provided_id, content=record.content
            )
        except ValueError as exc:
            plan.errors.append(f"{ref}: {exc}")
            plan.chunks_total += len(record.chunks)
            continue

        if record.provided_id:
            if planned_doc != record.provided_id:
                plan.document_changes_total += 1
                if len(plan.document_change_samples) < sample_limit:
                    plan.document_change_samples.append((record.provided_id, planned_doc))
        else:
            plan.minted_documents_total += 1
            if record.content is not None:
                plan.warnings.append(
                    f"{ref}: identity derived from content only — a content edit "
                    "forks this document into a new identity; give it a stable id"
                )

        if planned_doc in seen_documents:
            plan.warnings.append(
                f"{ref}: derives the same document id as record "
                f"#{seen_documents[planned_doc]} ({planned_doc}) — these records "
                "would silently merge; give them distinct ids/keys"
            )
        else:
            seen_documents[planned_doc] = position

        for index, chunk in enumerate(record.chunks):
            plan.chunks_total += 1
            if isinstance(chunk, str):
                chunk = ChunkRecord(existing_id=chunk)
            try:
                planned_chunk = strategy.chunk_id(
                    document_id=planned_doc,
                    index=index,
                    existing_id=chunk.existing_id,
                    content=chunk.content,
                )
            except ValueError as exc:
                plan.errors.append(f"{ref} chunk {index}: {exc}")
                continue

            if chunk.existing_id:
                if planned_chunk != chunk.existing_id:
                    plan.chunk_changes_total += 1
                    if len(plan.chunk_change_samples) < sample_limit:
                        plan.chunk_change_samples.append((chunk.existing_id, planned_chunk))
            else:
                plan.minted_chunks_total += 1

            if planned_chunk in seen_chunks:
                other_doc, other_index = seen_chunks[planned_chunk]
                plan.warnings.append(
                    f"{ref} chunk {index}: derives the same chunk id as record "
                    f"#{other_doc} chunk {other_index} ({planned_chunk}) — "
                    "retrievals of either would be indistinguishable"
                )
            else:
                seen_chunks[planned_chunk] = (position, index)

    return plan
