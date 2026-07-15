"""Official Hardshell client for sending AI/RAG telemetry and reading derived reports."""

from hardshell_telemetry._version import __version__
from hardshell_telemetry.chunking import (
    Chunker,
    FixedSizeChunker,
    ParagraphChunker,
    SentenceChunker,
)
from hardshell_telemetry.client import HardshellClient
from hardshell_telemetry.exceptions import TelemetryError
from hardshell_telemetry.intake import (
    DEFAULT_SENSITIVITY_SCALE,
    HARDSHELL_DOC_NAMESPACE,
    content_hash,
    derive_chunk_id,
    derive_document_id,
    sensitivity_from_level,
)
from hardshell_telemetry.types import (
    Chunk,
    ChunkAccessCount,
    Document,
    DocumentAccessReport,
    DocumentAccessSummary,
    DocumentLink,
    IngestChunksResult,
    IngestDocumentsResult,
    IngestSpansResult,
    RetrievalSpan,
    RetrievedChunk,
)

__all__ = [
    "DEFAULT_SENSITIVITY_SCALE",
    "HARDSHELL_DOC_NAMESPACE",
    "Chunk",
    "ChunkAccessCount",
    "Chunker",
    "Document",
    "DocumentAccessReport",
    "DocumentAccessSummary",
    "DocumentLink",
    "FixedSizeChunker",
    "HardshellClient",
    "IngestChunksResult",
    "IngestDocumentsResult",
    "IngestSpansResult",
    "ParagraphChunker",
    "RetrievalSpan",
    "RetrievedChunk",
    "SentenceChunker",
    "TelemetryError",
    "__version__",
    "content_hash",
    "derive_chunk_id",
    "derive_document_id",
    "sensitivity_from_level",
]
