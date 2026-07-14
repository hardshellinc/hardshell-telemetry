"""Errors raised by the Hardshell telemetry client."""

from __future__ import annotations

__all__ = ["TelemetryError"]


class TelemetryError(RuntimeError):
    """Raised when a request to the Hardshell API fails.

    Covers both transport failures (connection refused, timeout) and non-2xx
    HTTP responses. For HTTP failures, ``status_code`` holds the response
    status and ``detail`` holds the response body text, when available.

    The client raises by default so setup problems are visible during
    integration. In production, telemetry should usually be non-fatal — wrap
    calls so a flaky network never breaks your retrieval path:

        try:
            client.record_retrieval(...)
        except Exception:
            logging.warning("hardshell telemetry failed (non-fatal)", exc_info=True)
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
