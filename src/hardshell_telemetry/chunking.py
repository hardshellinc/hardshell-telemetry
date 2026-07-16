"""Composable text chunking.

A chunker is anything with ``chunk(text: str) -> list[str]`` — the
:class:`Chunker` protocol. The built-ins below cover the common strategies
with no dependencies; bring your own class for anything smarter, and the rest
of the library (the ingest front) composes with it unchanged.

Chunkers split text; they never embed, filter, or transform content beyond
trimming whitespace at split boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "Chunker",
    "FixedSizeChunker",
    "ParagraphChunker",
    "SentenceChunker",
]


@runtime_checkable
class Chunker(Protocol):
    """Anything that splits text into chunks."""

    def chunk(self, text: str) -> list[str]:
        """Split ``text`` into a list of chunk strings."""
        ...


@dataclass(frozen=True)
class FixedSizeChunker:
    """Character windows of ``size`` with ``overlap`` characters shared
    between neighbors.

    The blunt default: no notion of sentences or structure, but predictable
    chunk counts and full coverage. The final window may be shorter.
    """

    size: int
    """Window length in characters; the final window may be shorter."""

    overlap: int = 0
    """Characters shared between neighboring windows; must be smaller than ``size``."""

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"size must be positive, got {self.size}")
        if not 0 <= self.overlap < self.size:
            raise ValueError(
                f"overlap must be >= 0 and smaller than size ({self.size}), got {self.overlap}"
            )

    def chunk(self, text: str) -> list[str]:
        """Split ``text`` into character windows covering it end to end."""
        if not text:
            return []
        step = self.size - self.overlap
        chunks: list[str] = []
        start = 0
        while True:
            chunks.append(text[start : start + self.size])
            if start + self.size >= len(text):
                break
            start += step
        return chunks


def _pack(parts: list[str], max_chars: int | None, separator: str) -> list[str]:
    """Greedily pack parts into chunks of at most ``max_chars``.

    A single part longer than ``max_chars`` becomes its own chunk unsplit —
    packing never cuts inside a part.
    """
    if max_chars is None:
        return parts
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for part in parts:
        added = len(part) if not current else current_len + len(separator) + len(part)
        if current and added > max_chars:
            chunks.append(separator.join(current))
            current, current_len = [part], len(part)
        else:
            current.append(part)
            current_len = added
    if current:
        chunks.append(separator.join(current))
    return chunks


@dataclass(frozen=True)
class ParagraphChunker:
    """Split on blank lines (paragraph boundaries).

    With ``max_chars``, consecutive paragraphs are greedily packed into
    chunks up to that length (joined by a blank line); a single paragraph
    longer than ``max_chars`` stays whole — this chunker never cuts inside a
    paragraph.
    """

    max_chars: int | None = None
    """Greedy packing limit per chunk; ``None`` keeps one paragraph per chunk."""

    def __post_init__(self) -> None:
        if self.max_chars is not None and self.max_chars <= 0:
            raise ValueError(f"max_chars must be positive, got {self.max_chars}")

    def chunk(self, text: str) -> list[str]:
        """Split ``text`` at blank lines, packing paragraphs up to ``max_chars``."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return _pack(paragraphs, self.max_chars, "\n\n")


@dataclass(frozen=True)
class SentenceChunker:
    """Split on sentence boundaries (``.``, ``!``, ``?`` followed by
    whitespace).

    With ``max_chars``, consecutive sentences are greedily packed into chunks
    up to that length; a single sentence longer than ``max_chars`` stays
    whole. Boundary detection is intentionally simple (no abbreviation
    handling) — bring your own :class:`Chunker` for linguistic accuracy.
    """

    max_chars: int | None = None
    """Greedy packing limit per chunk; ``None`` keeps one sentence per chunk."""

    def __post_init__(self) -> None:
        if self.max_chars is not None and self.max_chars <= 0:
            raise ValueError(f"max_chars must be positive, got {self.max_chars}")

    def chunk(self, text: str) -> list[str]:
        """Split ``text`` at sentence boundaries, packing up to ``max_chars``."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        return _pack(sentences, self.max_chars, " ")
