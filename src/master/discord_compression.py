"""Discord outbound compression pipeline.

Exposes:
  - compress_for_discord: tiered compression pipeline for files too large for Discord
  - upload_result_files_to_discord: upload DispatchResult file_paths to a Discord channel
  - DISCORD_FREE_TIER_LIMIT_BYTES: 25 MiB limit for free/tier-1 guilds
  - DISCORD_NITRO_PREMIUM_TIER: minimum guild boost tier for the 500 MB cap
  - _make_zip: internal helper (exposed at module scope so tests can patch it)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DISCORD_FREE_TIER_LIMIT_BYTES: int = 25 * 1024 * 1024  # 25 MiB
DISCORD_NITRO_PREMIUM_TIER: int = 2  # guild boost tier 2+ → 500 MB cap


def _make_zip(src: Path, dest: Path) -> None:
    """Recompress *src* into a ZIP archive at *dest* using maximum deflate compression."""
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        zout.write(src, arcname=src.name)


def compress_for_discord(
    *,
    file_path: Path,
    guild_premium_tier: int,
) -> tuple[Path | None, str | None]:
    """Apply the tiered compression pipeline for Discord.

    Returns ``(result_path, None)`` when the file can be attached, or
    ``(None, notice)`` when all compression steps fail and a hard-failure
    message should be posted instead.

    Steps:
      1. Nitro fast-path: guild tier >= DISCORD_NITRO_PREMIUM_TIER → direct attach.
      2. File already within DISCORD_FREE_TIER_LIMIT_BYTES → direct attach.
      3. ZIP recompression via _make_zip.
      4. LibreOffice conversion + Ghostscript PDF compression.
      5. Hard failure → return notice string.
    """
    # Step 1: Nitro fast-path
    if guild_premium_tier >= DISCORD_NITRO_PREMIUM_TIER:
        return file_path, None

    size = file_path.stat().st_size

    # Step 2: already small enough
    if size <= DISCORD_FREE_TIER_LIMIT_BYTES:
        return file_path, None

    # Step 3: ZIP recompression
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            zip_path = Path(tmp.name)
        _make_zip(file_path, zip_path)
        if zip_path.stat().st_size <= DISCORD_FREE_TIER_LIMIT_BYTES:
            return zip_path, None
        zip_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    # Step 4: LibreOffice + Ghostscript
    if shutil.which("libreoffice") is not None:
        try:
            with tempfile.TemporaryDirectory() as conv_dir:
                subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", conv_dir, str(file_path)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                pdf_files = list(Path(conv_dir).glob("*.pdf"))
                if pdf_files:
                    pdf_path = pdf_files[0]
                    if shutil.which("gs") is not None:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                            compressed_pdf = Path(tmp_pdf.name)
                        subprocess.run(
                            [
                                "gs", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                                "-dPDFSETTINGS=/ebook",
                                f"-sOutputFile={compressed_pdf}", str(pdf_path),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        )
                        if compressed_pdf.stat().st_size <= DISCORD_FREE_TIER_LIMIT_BYTES:
                            return compressed_pdf, None
                        compressed_pdf.unlink(missing_ok=True)
                    elif pdf_path.stat().st_size <= DISCORD_FREE_TIER_LIMIT_BYTES:
                        stable = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                        stable.close()
                        shutil.copy2(str(pdf_path), stable.name)
                        return Path(stable.name), None
        except Exception:  # noqa: BLE001
            pass

    # Step 5: hard failure
    size_mb = size / 1_000_000
    notice = (
        f"File `{file_path.name}` is too large for Discord even after compression"
        f" ({size_mb:.1f} MB). It has been saved to the agent workspace at `{file_path}`."
        " Use Slack to retrieve it, or ask the agent to split it."
    )
    return None, notice


def upload_result_files_to_discord(
    *,
    channel: Any,
    file_paths: list[Path],
) -> None:
    """Upload files to a Discord channel using the sync client interface.

    This is a lightweight synchronous wrapper; the caller is responsible for
    running it inside an async context if needed.  An empty *file_paths* list
    is a no-op.
    """
    if not file_paths:
        return

    try:
        import discord  # type: ignore[import]
    except ImportError:
        LOGGER.warning("discord_compression.upload_skipped reason=discord_not_installed")
        return

    for path in file_paths:
        try:
            with open(path, "rb") as fh:
                channel.send(file=discord.File(fh, filename=path.name))
            LOGGER.info("discord_compression.file_uploaded filename=%s", path.name)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("discord_compression.file_upload_failed filename=%s error=%s", path.name, exc)
        finally:
            path.unlink(missing_ok=True)
