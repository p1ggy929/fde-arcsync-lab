"""Explicit contract for future archive/OCR/authorization integrations.

No upstream network integration is enabled in this local demonstration.
"""
from typing import Protocol

class ArchiveSource(Protocol):
    def authorized_documents(self, principal: str, since_version: str | None) -> list[dict]:
        """Return only records authorized for this principal, with immutable versions."""
        ...
    def page(self, principal: str, document_id: str, version: str, scan_page: int) -> bytes:
        """Revalidate authority and retrieve the exact version, or fail closed."""
        ...

class UpstreamUnavailable(RuntimeError):
    """Keep a timestamped authorized snapshot and route critical operations to humans."""
