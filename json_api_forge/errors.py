from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


def safe_error_text(value: Any) -> str:
    """Bound printable diagnostics; structured HTTP detail remains available separately."""
    # Truncating first can remove the final @ from URL credentials and leave
    # their private prefix visible. Redact the complete field, then bound it.
    result = str(value)
    result = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", result)
    result = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [redacted]", result)
    result = re.sub(
        r"(?i)(\b(?:password|passwd|token|secret|api[_-]?key|authorization)\b[\"']?\s*[:=]\s*)(?:[\"'][^\"'\r\n]*[\"']|[^\s,;&}]+)",
        r"\1[redacted]",
        result,
    )
    return "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in result[:4096])


class ForgeError(RuntimeError):
    """Base exception raised by the JSON API Forge client."""


class ForgeTransportError(ForgeError):
    """The server could not be reached or timed out."""


class ForgeResponseTooLarge(ForgeError):
    """A response exceeded the configured safety limit."""


class ForgeClusterUnavailable(ForgeError):
    """No configured cluster endpoint could complete the request."""


class ForgeIntegrationError(ForgeError):
    """An optional integration is missing or returned an incompatible value."""


class ForgeSessionError(ForgeError):
    """The Editor control-plane session is missing or malformed."""


@dataclass(slots=True)
class ForgeHTTPError(ForgeError):
    status_code: int
    detail: Any
    request_id: str | None = None
    retry_after: str | None = None

    def __str__(self) -> str:
        suffix = f" request_id={safe_error_text(self.request_id)[:128]}" if self.request_id else ""
        return f"JSON API Forge returned HTTP {self.status_code}: {safe_error_text(self.detail)}{suffix}"
