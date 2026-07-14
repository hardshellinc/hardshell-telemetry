"""Read back how often your documents' chunks are being retrieved.

Set HARDSHELL_API_KEY and HARDSHELL_BASE_URL first:

    HARDSHELL_API_KEY=hs-... HARDSHELL_BASE_URL=https://... \
        python examples/read_document_access.py
"""

import os
from datetime import UTC, datetime, timedelta

from hardshell_telemetry import TelemetryClient

client = TelemetryClient(
    api_key=os.environ["HARDSHELL_API_KEY"],
    base_url=os.environ["HARDSHELL_BASE_URL"],
)

report = client.document_access_report(
    window_start=datetime.now(UTC) - timedelta(days=7),
    limit=20,
)

print(f"{report.total_documents} documents registered; showing {len(report.documents)}\n")
for doc in report.documents:
    total = sum(c.access_count for c in doc.chunks)
    print(f"{doc.name or doc.document_id}: {total} retrievals across {doc.chunk_count} chunks")
    for chunk in doc.chunks:
        if chunk.access_count:
            print(f"  {chunk.chunk_id}: {chunk.access_count}")
