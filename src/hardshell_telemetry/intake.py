"""Deterministic identity and sensitivity helpers — the intake contract.

These pure functions define how document ids are derived. They are a
compatibility promise: the same inputs produce the same id on every machine,
every run, and every version of this library, so ids derived at index-build
time can always be re-derived later. The recipe, reproducible from any
language:

    HARDSHELL_DOC_NAMESPACE = uuid5(NAMESPACE_URL, "https://hardshell.ai/document")
    document_id = str(uuid5(HARDSHELL_DOC_NAMESPACE, natural_key))
    natural_key = your stable key if you have one, else sha256 hex of the content

Chunk ids are different: **if your vector store already has chunk ids,
register those verbatim** — the id your retrieval path reports is the join
key, and wrapping it would silently break the join. :func:`derive_chunk_id`
exists only for new indexes where no ids exist yet.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Final

__all__ = [
    "DEFAULT_SENSITIVITY_SCALE",
    "HARDSHELL_DOC_NAMESPACE",
    "content_hash",
    "derive_chunk_id",
    "derive_document_id",
    "sensitivity_from_level",
]

HARDSHELL_DOC_NAMESPACE: Final = uuid.UUID("0169b2a1-86c2-5d3a-a28c-45728827aa43")
"""The uuid5 namespace for derived document ids.

Equal to ``uuid5(NAMESPACE_URL, "https://hardshell.ai/document")`` — pinned as
a literal so a refactor can't silently change every derived id; the golden
tests hold this constant forever.
"""

DEFAULT_SENSITIVITY_SCALE: Final[tuple[str, ...]] = (
    "public",
    "low",
    "medium",
    "high",
    "critical",
)
"""Recommended sensitivity tiers, ordered least → most sensitive.

The default scale for :func:`sensitivity_from_level`. Free-form labels are
also fine everywhere labels are accepted.
"""


def content_hash(content: str | bytes) -> str:
    """Fingerprint document contents: sha256 hex (64 chars, lowercase).

    Strings are UTF-8 encoded. The result doubles as ``Document.content_hash``
    (change detection across re-ingests) and as the content-derived natural
    key inside :func:`derive_document_id`.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def derive_document_id(key: str | None = None, *, content: str | bytes | None = None) -> str:
    """Derive the deterministic document id Hardshell records live under.

    Args:
        key: Your natural key for the document — any stable string you can
            reproduce later, e.g. a path, URL, database key, or
            ``"<dataset>#row=<n>"``. Preferred when you have one: identity
            survives content edits. Empty strings count as absent.
        content: The document contents. Hashed locally (sha256) and used as
            the natural key when ``key`` is absent; **never transmitted**.

    Passing both is the recommended call — ``key`` wins for identity, and you
    can still send :func:`content_hash` for change detection. Same inputs →
    same id, forever (uuid5; recipe in the module docstring).
    """
    if key:
        natural_key = key
    elif content is not None:
        natural_key = content_hash(content)
    else:
        raise ValueError(
            "derive_document_id needs a natural key or the document content — "
            "pass key=<a stable string like a path, URL, or 'dataset#row=N'> "
            "and/or content=<the document text or bytes>"
        )
    return str(uuid.uuid5(HARDSHELL_DOC_NAMESPACE, natural_key))


def derive_chunk_id(
    document_id: str,
    index: int,
    *,
    content: str | bytes | None = None,
    separator: str = ":",
    index_width: int = 4,
    content_hash_chars: int = 8,
) -> str:
    """Mint a chunk id for a NEW index — greenfield only.

    If your vector store already has chunk ids, do not use this: register
    the store's ids verbatim, because the id your retrieval path reports is
    the join key.

    The default format is ``"{document_id}:{index:04d}"``, plus ``":{first 8
    hash chars}"`` when ``content`` is given so re-chunked content is
    distinguishable across index rebuilds. ``separator`` / ``index_width`` /
    ``content_hash_chars`` adjust the format (pick once and never change —
    the format is part of your ids). Deterministic: same inputs → same id.
    Store this exact string in your vector store (as the record id or in its
    metadata).
    """
    if not document_id:
        raise ValueError("derive_chunk_id needs the parent document_id")
    if index < 0:
        raise ValueError(f"chunk index must be >= 0, got {index}")
    base = f"{document_id}{separator}{index:0{index_width}d}"
    if content is not None:
        return f"{base}{separator}{content_hash(content)[:content_hash_chars]}"
    return base


def sensitivity_from_level(
    level: str,
    *,
    scale: Sequence[str] = DEFAULT_SENSITIVITY_SCALE,
) -> float:
    """Map an ordered sensitivity label onto the API's 0–1 ``sensitivity`` scale.

    Levels are spaced evenly: with the default scale, ``"public"`` → 0.0,
    ``"low"`` → 0.25, ``"medium"`` → 0.5, ``"high"`` → 0.75, ``"critical"`` →
    1.0. Matching is case-insensitive and ignores surrounding whitespace.

    Using your own vocabulary? Pass ``scale=`` with your tiers ordered least →
    most sensitive. This function is never called implicitly — the client
    sends only the values you give it.
    """
    if len(scale) < 2:
        raise ValueError("scale needs at least two levels, ordered least to most sensitive")
    levels = [s.strip().lower() for s in scale]
    normalized = level.strip().lower()
    try:
        position = levels.index(normalized)
    except ValueError:
        raise ValueError(
            f"unknown sensitivity level {level!r} — expected one of {tuple(scale)}; "
            "if you use your own tiers, pass scale=(...) ordered least to most sensitive"
        ) from None
    return position / (len(levels) - 1)
