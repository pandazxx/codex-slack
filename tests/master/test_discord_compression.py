"""Tests for the Discord outbound compression pipeline and platform handler stubs.

Covers:
  - DP-01 to DP-08: Discord tiered compression pipeline
  - DC-01 to DC-05: Discord inbound handler helpers (mocked)
  - SK-01 to SK-03: Slack outbound file upload helper
  - IC-03 / IC-04: platform handlers do not crash on empty file_paths

The Discord and Slack platform clients are fully mocked; no real HTTP or
Discord/Slack SDK calls are made.

All imports of not-yet-implemented modules are deferred to the individual test
methods.  This means pytest can collect all test IDs even when the engineer
has not yet landed the implementation — only the individual tests fail, not
the entire module.

Spec reference: docs/decisions/0001-rich-file-attachment-support.md
Test plan:      docs/test-plans/rich-file-attachments.md  (sections 4, 5, 6)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_25_MB = 25 * 1024 * 1024
_20_MB = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path: Path, size_bytes: int, content: bytes = b"\x00") -> Path:
    path.write_bytes(content * size_bytes)
    return path


# ---------------------------------------------------------------------------
# DP-01  Nitro tier 2+ → direct attach, no compression
# ---------------------------------------------------------------------------

class TestNitroFastPath:
    def test_nitro_tier_skips_compression_and_attaches_directly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-01: guild Nitro tier ≥ DISCORD_NITRO_PREMIUM_TIER → direct attach."""
        from src.master.discord_compression import (  # type: ignore[import]
            DISCORD_NITRO_PREMIUM_TIER,
            compress_for_discord,
        )

        big_file = _make_file(tmp_path / "report.docx", _25_MB + 1)
        compress_called: list[bool] = []

        def fake_make_zip(src: Path, dest: Path) -> None:
            compress_called.append(True)

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            result_path, notice = compress_for_discord(
                file_path=big_file,
                guild_premium_tier=DISCORD_NITRO_PREMIUM_TIER,
            )

        assert not compress_called, "must not ZIP on Nitro fast-path"
        assert result_path == big_file
        assert notice is None


# ---------------------------------------------------------------------------
# DP-02  File under 25 MB → direct attach
# ---------------------------------------------------------------------------

class TestSmallFileFastPath:
    def test_file_under_limit_attached_directly(self, tmp_path: Path) -> None:
        """DP-02: file ≤ 25 MB, no Nitro → direct attach, no compression."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        small_file = _make_file(tmp_path / "small.xlsx", _25_MB - 1)

        result_path, notice = compress_for_discord(
            file_path=small_file,
            guild_premium_tier=0,
        )

        assert result_path == small_file
        assert notice is None


# ---------------------------------------------------------------------------
# DP-03 / DP-04  File over 25 MB → ZIP recompression
# ---------------------------------------------------------------------------

class TestZipRecompression:
    def test_zip_compression_attempted_for_oversized_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-03 / DP-04: oversized file triggers ZIP recompression; small zip is attached."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        big_file = _make_file(tmp_path / "big.docx", _25_MB + 1)

        def fake_make_zip(src: Path, dest: Path) -> None:
            # Produce a zip smaller than 25 MB.
            dest.write_bytes(b"z" * (_25_MB - 100))

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            result_path, notice = compress_for_discord(
                file_path=big_file,
                guild_premium_tier=0,
            )

        assert result_path is not None
        assert result_path.suffix == ".zip"
        assert notice is None

    def test_zip_still_too_large_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-05: zip still over the limit → pipeline continues to hard failure."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        big_file = _make_file(tmp_path / "huge.docx", _25_MB + 1)

        def fake_make_zip(src: Path, dest: Path) -> None:
            dest.write_bytes(b"z" * (_25_MB + 1))  # still too big

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            with patch("src.master.discord_compression.shutil.which", return_value=None):
                result_path, notice = compress_for_discord(
                    file_path=big_file,
                    guild_premium_tier=0,
                )

        assert result_path is None
        assert notice is not None
        assert "huge.docx" in notice or str(big_file) in notice


# ---------------------------------------------------------------------------
# DP-06  LibreOffice absent → step 4 skipped
# ---------------------------------------------------------------------------

class TestLibreOfficeAbsent:
    def test_libreoffice_absent_skips_conversion_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-06: no LibreOffice on PATH → step 4 skipped; falls to hard-failure notice."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        big_file = _make_file(tmp_path / "report.docx", _25_MB + 1)

        def fake_make_zip(src: Path, dest: Path) -> None:
            dest.write_bytes(b"z" * (_25_MB + 1))

        def fake_which(name: str) -> str | None:
            return None if name == "libreoffice" else f"/usr/bin/{name}"

        libreoffice_called: list[bool] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            if "libreoffice" in cmd[0]:
                libreoffice_called.append(True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            with patch("src.master.discord_compression.shutil.which", fake_which):
                with patch("src.master.discord_compression.subprocess.run", fake_run):
                    result_path, notice = compress_for_discord(
                        file_path=big_file,
                        guild_premium_tier=0,
                    )

        assert not libreoffice_called, "libreoffice must not be invoked when absent"
        assert result_path is None
        assert notice is not None


# ---------------------------------------------------------------------------
# DP-07 / DP-08  Hard failure → message posted, no exception raised
# ---------------------------------------------------------------------------

class TestHardFailure:
    def test_hard_failure_returns_notice_not_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-07: all steps fail → returns a notice string, does not raise."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        big_file = _make_file(tmp_path / "unstoppable.pdf", _25_MB + 1)

        def fake_make_zip(src: Path, dest: Path) -> None:
            dest.write_bytes(b"z" * (_25_MB + 1))

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            with patch("src.master.discord_compression.shutil.which", return_value=None):
                result_path, notice = compress_for_discord(
                    file_path=big_file,
                    guild_premium_tier=0,
                )

        assert result_path is None
        assert notice is not None

    def test_hard_failure_notice_contains_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DP-08: hard-failure notice must include the workspace file path."""
        from src.master.discord_compression import compress_for_discord  # type: ignore[import]

        big_file = _make_file(tmp_path / "output.xlsx", _25_MB + 1)

        def fake_make_zip(src: Path, dest: Path) -> None:
            dest.write_bytes(b"z" * (_25_MB + 1))

        with patch("src.master.discord_compression._make_zip", fake_make_zip):
            with patch("src.master.discord_compression.shutil.which", return_value=None):
                _, notice = compress_for_discord(
                    file_path=big_file,
                    guild_premium_tier=0,
                )

        assert notice is not None
        assert "output.xlsx" in notice or str(big_file) in notice


# ---------------------------------------------------------------------------
# IC-04  Discord upload helper: empty file_paths → no crash
# ---------------------------------------------------------------------------

class TestDiscordUploadHelperEmptyFilePaths:
    def test_upload_result_files_to_discord_empty_list_no_crash(self) -> None:
        """IC-04: calling the Discord upload helper with empty file_paths must not raise."""
        from src.master.discord_compression import upload_result_files_to_discord  # type: ignore[import]

        fake_channel = MagicMock()
        upload_result_files_to_discord(channel=fake_channel, file_paths=[])
        fake_channel.send.assert_not_called()

    def test_discord_free_tier_limit_is_25_mb(self) -> None:
        """Sanity-check: DISCORD_FREE_TIER_LIMIT_BYTES must equal 25 * 1024 * 1024."""
        from src.master.discord_compression import DISCORD_FREE_TIER_LIMIT_BYTES  # type: ignore[import]

        assert DISCORD_FREE_TIER_LIMIT_BYTES == _25_MB

    def test_nitro_premium_tier_constant_is_at_least_2(self) -> None:
        """Sanity-check: DISCORD_NITRO_PREMIUM_TIER must be ≥ 2 per Discord's tier model."""
        from src.master.discord_compression import DISCORD_NITRO_PREMIUM_TIER  # type: ignore[import]

        assert isinstance(DISCORD_NITRO_PREMIUM_TIER, int)
        assert DISCORD_NITRO_PREMIUM_TIER >= 2


# ---------------------------------------------------------------------------
# Discord inbound handler stubs — DC-01 to DC-05
# ---------------------------------------------------------------------------

class TestDiscordInboundHandler:
    """Contract tests for the Discord inbound attachment helpers.

    The actual Discord client is fully mocked.
    """

    def test_non_allowlisted_type_is_accepted(self) -> None:
        """DC-01: .docx / .xlsx / .pdf must be accepted (not blocked by allowlist)."""
        from src.master.discord_inbound import is_attachment_accepted  # type: ignore[import]

        assert is_attachment_accepted("report.docx", size_bytes=1024) is True
        assert is_attachment_accepted("data.xlsx", size_bytes=1024) is True
        assert is_attachment_accepted("scan.pdf", size_bytes=1024) is True

    def test_attachment_at_20mb_is_accepted(self) -> None:
        """DC-02: exactly 20 MB is within the new size limit."""
        from src.master.discord_inbound import is_attachment_accepted  # type: ignore[import]

        assert is_attachment_accepted("big.docx", size_bytes=_20_MB) is True

    def test_attachment_over_20mb_is_rejected(self) -> None:
        """DC-03: 20 MB + 1 byte must be rejected."""
        from src.master.discord_inbound import is_attachment_accepted  # type: ignore[import]

        assert is_attachment_accepted("too_big.docx", size_bytes=_20_MB + 1) is False

    def test_cdn_url_bytes_are_downloaded_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DC-04: CDN URL is fetched immediately; raw bytes are returned."""
        from src.master.discord_inbound import download_attachment  # type: ignore[import]

        fake_bytes = b"fake docx content"

        def fake_urlopen(req: object, timeout: int = 30) -> object:
            response = MagicMock()
            response.read.return_value = fake_bytes
            response.__enter__ = lambda s: s
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("src.master.discord_inbound.urlopen", fake_urlopen)

        result = download_attachment(
            "https://cdn.discordapp.com/attachments/123/456/report.docx"
        )

        assert result == fake_bytes

    def test_cdn_download_failure_returns_none_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DC-05: network error during CDN download → returns None, does not raise."""
        from src.master.discord_inbound import download_attachment  # type: ignore[import]

        def fake_urlopen(req: object, timeout: int = 30) -> object:
            raise OSError("network error")

        monkeypatch.setattr("src.master.discord_inbound.urlopen", fake_urlopen)

        result = download_attachment(
            "https://cdn.discordapp.com/attachments/123/456/report.docx"
        )

        assert result is None


# ---------------------------------------------------------------------------
# SK-01 to SK-03  Slack outbound file upload helper
# ---------------------------------------------------------------------------

class TestSlackOutboundUpload:
    def test_files_upload_v2_called_once_per_file_path(self) -> None:
        """SK-01: one files_upload_v2 call per path in file_paths."""
        from src.master.slack_uploader import upload_result_files_to_slack  # type: ignore[import]

        fake_client = MagicMock()
        paths = [Path("/workspace/out/report.docx"), Path("/workspace/out/chart.xlsx")]

        upload_result_files_to_slack(
            slack_client=fake_client,
            channel_id="CAGENT",
            file_paths=paths,
        )

        assert fake_client.files_upload_v2.call_count == 2

    def test_upload_uses_correct_channel_id(self) -> None:
        """SK-01 variant: the upload call references the correct channel."""
        from src.master.slack_uploader import upload_result_files_to_slack  # type: ignore[import]

        fake_client = MagicMock()

        upload_result_files_to_slack(
            slack_client=fake_client,
            channel_id="C_TARGET",
            file_paths=[Path("/workspace/out/only.pdf")],
        )

        call_kwargs = fake_client.files_upload_v2.call_args_list[0][1]
        channel_used = call_kwargs.get("channel") or call_kwargs.get("channel_id")
        assert channel_used == "C_TARGET"

    def test_empty_file_paths_makes_no_upload_call(self) -> None:
        """SK-02 / IC-03: empty file_paths → files_upload_v2 is never called."""
        from src.master.slack_uploader import upload_result_files_to_slack  # type: ignore[import]

        fake_client = MagicMock()

        upload_result_files_to_slack(
            slack_client=fake_client,
            channel_id="CAGENT",
            file_paths=[],
        )

        fake_client.files_upload_v2.assert_not_called()

    def test_upload_exception_does_not_propagate(self) -> None:
        """SK-03: files_upload_v2 raises → error is absorbed, no re-raise."""
        from src.master.slack_uploader import upload_result_files_to_slack  # type: ignore[import]

        fake_client = MagicMock()
        fake_client.files_upload_v2.side_effect = RuntimeError("Slack API error")

        # Must not raise.
        upload_result_files_to_slack(
            slack_client=fake_client,
            channel_id="CAGENT",
            file_paths=[Path("/workspace/out/report.docx")],
        )
