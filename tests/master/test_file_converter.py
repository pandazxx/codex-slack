"""Unit tests for src/master/file_converter.py.

These tests exercise the public API as it exists / is specified in ADR-0001:
  - convert_attachment(filename, mime_type, data) → str
  - attachment_to_prompt_fragment(filename, mime_type, data, ...) → str
  - ATTACHMENT_INLINE_TOKEN_BUDGET: int

Spec reference: docs/decisions/0001-rich-file-attachment-support.md
Test plan:      docs/test-plans/rich-file-attachments.md  (section 1)
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.master.file_converter import (
    ATTACHMENT_INLINE_TOKEN_BUDGET,
    attachment_to_prompt_fragment,
    convert_attachment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _para(text: str) -> MagicMock:
    p = MagicMock()
    p.text = text
    return p


def _fake_docx(paragraphs: list[str]) -> MagicMock:
    doc = MagicMock()
    doc.paragraphs = [_para(t) for t in paragraphs]
    return doc


# ---------------------------------------------------------------------------
# FC-01  docx — happy path
# ---------------------------------------------------------------------------

class TestDocxConversion:
    def test_docx_returns_extracted_text(self) -> None:
        """FC-01: a .docx file with plain text returns that text."""
        fake_doc = _fake_docx(["Hello from docx", "Second paragraph"])

        with patch("src.master.file_converter.docx.Document", return_value=fake_doc):
            result = convert_attachment("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"fake")

        assert "Hello from docx" in result
        assert "[attachment:" not in result  # not a skip notice

    def test_docx_format_hint_appended_for_low_text_yield(self) -> None:
        """FC-08: complex docx (tiny extracted text) appends the format hint."""
        # Only a few characters — below _COMPLEX_DOCX_TEXT_THRESHOLD (200).
        fake_doc = _fake_docx(["hi"])

        with patch("src.master.file_converter.docx.Document", return_value=fake_doc):
            result = convert_attachment("complex.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"x" * 50_000)

        assert "For best results" in result
        assert ".md" in result or ".txt" in result

    def test_docx_no_format_hint_when_text_yield_is_normal(self) -> None:
        """FC-09: a docx with plenty of extracted text does not get the hint."""
        long_text = "word " * 500  # well over 200 chars
        fake_doc = _fake_docx([long_text])

        with patch("src.master.file_converter.docx.Document", return_value=fake_doc):
            result = convert_attachment("normal.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"x" * len(long_text))

        assert "For best results" not in result

    def test_docx_detected_by_extension_regardless_of_mime(self) -> None:
        """FC-01 variant: .docx is routed to docx conversion by extension."""
        fake_doc = _fake_docx(["Extension-detected"])

        with patch("src.master.file_converter.docx.Document", return_value=fake_doc):
            result = convert_attachment("file.docx", "application/octet-stream", b"fake")

        assert "Extension-detected" in result


# ---------------------------------------------------------------------------
# FC-02  xlsx — happy path
# ---------------------------------------------------------------------------

class TestXlsxConversion:
    def test_xlsx_returns_text_representation(self) -> None:
        """FC-02: .xlsx returns a text/table representation."""
        fake_wb = MagicMock()
        fake_ws = MagicMock()
        fake_ws.iter_rows.return_value = [
            (MagicMock(value="Name"), MagicMock(value="Score")),
            (MagicMock(value="Alice"), MagicMock(value=95)),
        ]
        fake_wb.sheetnames = ["Sheet1"]
        fake_wb.__getitem__ = lambda self, key: fake_ws  # type: ignore[assignment]

        with patch("src.master.file_converter.openpyxl.load_workbook", return_value=fake_wb):
            result = convert_attachment("data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"fake")

        assert "Name" in result
        assert "Alice" in result
        assert "95" in result

    def test_xlsx_detected_by_extension(self) -> None:
        """FC-02 variant: .xlsx routed by extension even if mime is generic."""
        fake_wb = MagicMock()
        fake_ws = MagicMock()
        fake_ws.iter_rows.return_value = [(MagicMock(value="x"),)]
        fake_wb.sheetnames = ["S1"]
        fake_wb.__getitem__ = lambda self, key: fake_ws  # type: ignore[assignment]

        with patch("src.master.file_converter.openpyxl.load_workbook", return_value=fake_wb):
            result = convert_attachment("data.xlsx", "application/octet-stream", b"fake")

        assert "x" in result


# ---------------------------------------------------------------------------
# FC-03  csv — happy path
# ---------------------------------------------------------------------------

class TestCsvConversion:
    def test_csv_returns_raw_text(self) -> None:
        """FC-03: .csv is returned as plain text."""
        csv_bytes = b"id,value\n1,foo\n2,bar\n"
        result = convert_attachment("records.csv", "text/csv", csv_bytes)

        assert "id,value" in result
        assert "foo" in result
        assert "bar" in result

    def test_csv_detected_by_mime(self) -> None:
        """FC-03 variant: text/csv mime type routes to CSV conversion."""
        result = convert_attachment("data", "text/csv", b"a,b\n1,2\n")
        assert "a,b" in result


# ---------------------------------------------------------------------------
# FC-04  pdf — happy path
# ---------------------------------------------------------------------------

class TestPdfConversion:
    def test_pdf_returns_extracted_text(self) -> None:
        """FC-04: .pdf with extractable text returns that text."""
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Extracted from PDF"
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("src.master.file_converter.pdfplumber.open", return_value=fake_pdf):
            result = convert_attachment("document.pdf", "application/pdf", b"fake")

        assert "Extracted from PDF" in result

    def test_pdf_detected_by_extension(self) -> None:
        """FC-04 variant: .pdf extension routes to PDF conversion."""
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "From PDF by extension"
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("src.master.file_converter.pdfplumber.open", return_value=fake_pdf):
            result = convert_attachment("file.pdf", "application/octet-stream", b"fake")

        assert "From PDF by extension" in result


# ---------------------------------------------------------------------------
# FC-05 / FC-06  unsupported types → skip notice
# ---------------------------------------------------------------------------

class TestUnsupportedTypeSkipNotice:
    def test_zip_file_returns_skip_notice(self) -> None:
        """FC-05: unsupported .zip returns a skip notice."""
        result = convert_attachment("archive.zip", "application/zip", b"PK fake zip")

        assert "[attachment:" in result
        assert "archive.zip" in result
        assert "skipped" in result or "unsupported" in result

    def test_unknown_mime_returns_skip_notice(self) -> None:
        """FC-06: binary file with an unrecognised mime type returns a skip notice."""
        result = convert_attachment("mystery.bin", "application/octet-stream", b"\x00\x01")

        assert "[attachment:" in result
        assert "mystery.bin" in result

    def test_no_extension_and_unknown_mime_returns_skip_notice(self) -> None:
        """FC-06 variant: file with no extension and unknown mime returns skip notice."""
        result = convert_attachment("noextension", "application/octet-stream", b"raw bytes")

        assert "[attachment:" in result
        assert "noextension" in result


# ---------------------------------------------------------------------------
# FC-07  library exception → no propagation
# ---------------------------------------------------------------------------

class TestConverterExceptionHandling:
    def test_docx_library_exception_returns_notice_not_raises(self) -> None:
        """FC-07: if docx.Document raises, the caller gets a notice, not an exception."""
        with patch("src.master.file_converter.docx.Document", side_effect=Exception("corrupt zip")):
            # Must not raise.
            result = convert_attachment("broken.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"garbage")

        # Either a library-unavailable notice or a general skip notice.
        assert "[attachment:" in result

    def test_pdf_library_exception_returns_notice_not_raises(self) -> None:
        """FC-07 variant: pdfplumber exception produces a notice, not an exception."""
        with patch("src.master.file_converter.pdfplumber.open", side_effect=Exception("bad pdf")):
            result = convert_attachment("broken.pdf", "application/pdf", b"garbage")

        assert "[attachment:" in result


# ---------------------------------------------------------------------------
# FC-10 / FC-11  token budget threshold — via attachment_to_prompt_fragment
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_file_under_budget_is_injected_inline(self) -> None:
        """FC-10: extracted text under the budget is returned inline."""
        small_csv = b"a,b\n1,2\n"

        result = attachment_to_prompt_fragment(
            filename="small.csv",
            mime_type="text/csv",
            data=small_csv,
        )

        # Should contain the actual text, not a staged-pointer notice.
        assert "a,b" in result
        # Must not be a staged-pointer when there's no container_name.
        assert "Read tool" not in result

    def test_file_over_budget_with_container_returns_staged_pointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FC-11: extracted text over the budget returns a staged-pointer notice."""
        # Build CSV data whose extracted text is larger than ATTACHMENT_INLINE_TOKEN_BUDGET * 4
        # (the function converts chars to tokens via len // 4).
        oversized_chars = (ATTACHMENT_INLINE_TOKEN_BUDGET + 100) * 4
        big_csv = ("x," * 50 + "\n") * (oversized_chars // 102 + 1)
        big_csv_bytes = big_csv.encode()

        # Mock podman commands so we don't need a real container.
        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("src.master.file_converter.subprocess.run", fake_run)

        result = attachment_to_prompt_fragment(
            filename="large.csv",
            mime_type="text/csv",
            data=big_csv_bytes,
            container_name="agent-payments",
        )

        # Over budget with a container → staged pointer notice.
        assert "large.csv" in result
        assert "Read tool" in result or "placed at" in result or "/tmp/uploads" in result

    def test_file_over_budget_without_container_still_returns_inline(self) -> None:
        """FC-11 variant: over budget but no container_name → falls back to inline."""
        oversized_chars = (ATTACHMENT_INLINE_TOKEN_BUDGET + 100) * 4
        big_csv = ("y," * 50 + "\n") * (oversized_chars // 102 + 1)

        result = attachment_to_prompt_fragment(
            filename="large_no_container.csv",
            mime_type="text/csv",
            data=big_csv.encode(),
            container_name=None,
        )

        # Without a container we can't stage, so inline fallback applies.
        assert "large_no_container.csv" in result

    def test_budget_constant_is_positive_int(self) -> None:
        """Sanity-check: ATTACHMENT_INLINE_TOKEN_BUDGET is a usable positive integer."""
        assert isinstance(ATTACHMENT_INLINE_TOKEN_BUDGET, int)
        assert ATTACHMENT_INLINE_TOKEN_BUDGET > 0


# ---------------------------------------------------------------------------
# IC-02  stage_attachment_to_container calls podman cp correctly
# ---------------------------------------------------------------------------

class TestStagingPodmanCp:
    def test_stage_attachment_issues_podman_cp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IC-02: stage_attachment_to_container must issue a 'podman cp' command
        with the correct container name and destination path."""
        from src.master.file_converter import stage_attachment_to_container

        seen_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            seen_cmds.append(list(cmd))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("src.master.file_converter.subprocess.run", fake_run)

        container_path = stage_attachment_to_container(
            filename="report.docx",
            data=b"fake docx bytes",
            container_name="agent-payments",
        )

        cp_calls = [cmd for cmd in seen_cmds if len(cmd) >= 2 and cmd[0] == "podman" and cmd[1] == "cp"]
        assert len(cp_calls) >= 1, f"Expected at least one 'podman cp' call, got: {seen_cmds}"

        cp_cmd = cp_calls[0]
        # Destination must be <container_name>:<path>
        dest_arg = cp_cmd[-1]
        assert dest_arg.startswith("agent-payments:"), (
            f"podman cp destination must start with 'agent-payments:', got: {dest_arg!r}"
        )
        assert "report.docx" in dest_arg

    def test_stage_attachment_returns_container_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IC-02 variant: the returned path is the in-container destination."""
        from src.master.file_converter import stage_attachment_to_container

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("src.master.file_converter.subprocess.run", fake_run)

        container_path = stage_attachment_to_container(
            filename="data.csv",
            data=b"a,b\n1,2\n",
            container_name="agent-payments",
        )

        assert "data.csv" in container_path
        # Path should be absolute inside the container.
        assert container_path.startswith("/")
