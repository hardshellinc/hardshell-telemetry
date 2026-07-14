"""Official Hardshell client for sending AI/RAG telemetry and reading derived reports."""

from hardshell_telemetry.client import TelemetryClient
from hardshell_telemetry.exceptions import TelemetryError
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

__version__ = "0.0.0"

__all__ = [
    "Chunk",
    "ChunkAccessCount",
    "Document",
    "DocumentAccessReport",
    "DocumentAccessSummary",
    "DocumentLink",
    "IngestChunksResult",
    "IngestDocumentsResult",
    "IngestSpansResult",
    "RetrievalSpan",
    "RetrievedChunk",
    "TelemetryClient",
    "TelemetryError",
    "__version__",
]
