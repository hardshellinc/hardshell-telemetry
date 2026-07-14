"""Send retrieval telemetry after each vector-store query.

This is the hot-path call: after your vector store returns its top-k chunks,
report which chunks the user's query touched. Set HARDSHELL_API_KEY and
HARDSHELL_BASE_URL first:

    HARDSHELL_API_KEY=hs-... HARDSHELL_BASE_URL=https://... \
        python examples/record_retrieval.py
"""

import logging
import os

from hardshell_telemetry import HardshellClient

client = HardshellClient(
    api_key=os.environ["HARDSHELL_API_KEY"],
    base_url=os.environ["HARDSHELL_BASE_URL"],
    source="production",  # provenance label stamped on everything this client sends
)

# After a retrieval — chunks as (chunk_id, score) with scores in [0, 1]:
result = client.record_retrieval(
    chunks=[("employee-handbook:0001", 0.91), ("employee-handbook:0002", 0.88)],
    user_id="end-user-123",  # the identity detection keys on — send it if you have it
    session_id="session-456",
    backend="chroma",
)
print(f"spans accepted: {result.spans_accepted}, chunks logged: {result.chunks_logged}")

# In production, make telemetry non-fatal so a network blip never breaks a
# user's retrieval:
try:
    client.record_retrieval(chunks=[("employee-handbook:0001", 0.93)], user_id="end-user-123")
except Exception:
    logging.warning("hardshell telemetry failed (non-fatal)", exc_info=True)
