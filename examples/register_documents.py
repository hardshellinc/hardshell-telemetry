"""Register documents and chunks so retrievals can be joined to metadata.

Run this once per corpus build (or whenever documents/chunks change), not per
query. Set HARDSHELL_API_KEY and HARDSHELL_BASE_URL first:

    HARDSHELL_API_KEY=hs-... HARDSHELL_BASE_URL=https://... \
        python examples/register_documents.py
"""

import os

from hardshell_telemetry import Chunk, Document, DocumentLink, TelemetryClient

client = TelemetryClient(
    api_key=os.environ["HARDSHELL_API_KEY"],
    base_url=os.environ["HARDSHELL_BASE_URL"],
)

# 1. Register the source document. Optional fields (sensitivity, metadata)
#    make reports and detection richer — send what you have.
documents = client.ingest_documents(
    [
        Document(
            document_id="employee-handbook",
            name="Employee Handbook (2026)",
            sensitivity=0.4,
            sensitivity_level="internal",
            custom_metadata={"owner": "people-ops", "version": "2026.1"},
        )
    ]
)
print(f"documents upserted: {documents.documents_upserted}")

# 2. Register its chunks, linked back to the document. Use the SAME chunk ids
#    your vector store returns at query time — that id is the join key.
chunks = client.ingest_chunks(
    [
        Chunk(
            chunk_id="employee-handbook:0001",
            sensitivity_level="internal",
            document_links=[DocumentLink(document_id="employee-handbook")],
        ),
        Chunk(
            chunk_id="employee-handbook:0002",
            sensitivity_level="internal",
            document_links=[DocumentLink(document_id="employee-handbook")],
        ),
    ]
)
print(f"chunks upserted: {chunks.chunks_upserted} (links: {chunks.links_upserted})")
