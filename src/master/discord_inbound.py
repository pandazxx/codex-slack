"""Discord inbound attachment helpers.

Exposes:
  - is_attachment_accepted: size-gate for inbound Discord attachments
  - download_attachment: download a CDN URL and return raw bytes
  - urlopen: exposed at module scope so tests can patch it
"""
from __future__ import annotations

import logging
from urllib.request import Request, urlopen  # noqa: F401  (urlopen patched in tests)

LOGGER = logging.getLogger(__name__)

_MAX_ATTACHMENT_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MiB


def is_attachment_accepted(filename: str, *, size_bytes: int) -> bool:
    """Return True if the attachment is within the accepted size limit.

    Any file type is accepted; only the size is gated.
    """
    return size_bytes <= _MAX_ATTACHMENT_SIZE_BYTES


def download_attachment(url: str) -> bytes | None:
    """Download a Discord CDN attachment URL and return the raw bytes.

    Returns ``None`` on any network or I/O error (does not raise).
    """
    try:
        req = Request(url, headers={"User-Agent": "codex-master/1.0"})
        with urlopen(req, timeout=30) as response:
            return bytes(response.read())
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("discord_inbound.download_failed url=%s error=%s", url, exc)
        return None
