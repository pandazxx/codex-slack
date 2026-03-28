# Design: Inbound File Handling (docx / pdf)

**Status:** In progress — feature branch `feat/inbound-file-handling`
**Date:** 2026-03-28

---

## Context

This feature adds inbound file attachment support to the agent platform. When a user sends a docx, pdf, xlsx, or csv file via Discord or Slack, the master runtime converts it to text and injects it into the agent's prompt. Agents receive clean, structured text without needing to handle binary file formats themselves.

---

## Goals

- Support docx, pdf, xlsx, and csv inbound attachments on both Discord and Slack
- Extract text content from each format with zero friction for the user
- Gracefully handle edge cases: embedded images, oversized content, missing libraries
- Provide consistent, clear fallback notices when content cannot be fully extracted

## Non-Goals

- Extracting or rendering images embedded inside docx or pdf files (requires LibreOffice; deferred)
- Supporting every possible file format at launch
- Modifying the agent container — all conversion runs in the master runtime

---

## Design

### Conversion pipeline (`src/master/file_converter.py`)

A single entry point `attachment_to_prompt_fragment()` handles all attachment types:

1. `convert_attachment(filename, mime_type, data) → str` — dispatches by extension or MIME type to a format-specific converter
2. If the result fits within `ATTACHMENT_INLINE_TOKEN_BUDGET` (default 4000 tokens), it is injected inline as `[attachment: <filename>]\n<text>`
3. If it exceeds the budget and a container is available, the file is staged via `podman cp` and a Read-tool pointer is returned instead

**Format converters:**

| Format | Library | Text extraction | Images |
|---|---|---|---|
| `.docx` | `python-docx` | Paragraph text | Detected; fallback hint shown |
| `.xlsx` | `openpyxl` | All sheets as markdown tables | N/A |
| `.csv` | stdlib | Raw text | N/A |
| `.pdf` | `pdfplumber` | Page text and tables | Detected; fallback hint shown |
| Other | — | Unsupported notice | — |

### Embedded image handling

Neither `python-docx` nor `pdfplumber` can render images to a format the agent can see. When images are detected, a consistent fallback hint is shown:

```
For best results with text + images, send a .md or .txt file and attach images separately.
```

**docx:** hint is shown when extracted text is short (heuristic: likely an image-heavy document).
**pdf:** hint is shown when `page.images` is non-empty on any page (explicit detection).

Both paths use the same `_IMAGE_FALLBACK_HINT` constant.

### Known gap: PDF embedded images — silent drop (bug)

In the initial implementation, `_convert_pdf` extracts text via `pdfplumber` but does not check `page.images`. If a PDF contains images with no text, the result is an empty string with no notice to the user.

**Fix:** Check `page.images` for each page. If any images are found, append `_IMAGE_FALLBACK_HINT` to the result.

---

## Changes Required

### `src/master/file_converter.py`

1. Extract the hint string to a module-level constant `_IMAGE_FALLBACK_HINT`.
2. Update `_convert_docx` to use the constant.
3. Update `_convert_pdf` to check `page.images` and append the hint when images are detected.

```python
_IMAGE_FALLBACK_HINT = (
    "For best results with text + images, send a .md or .txt file"
    " and attach images separately."
)

def _convert_pdf(data: bytes) -> str:
    ...
    has_images = False
    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)
        if page.images:
            has_images = True
    result = "\n\n".join(pages)
    if has_images:
        result = f"{result}\n\n{_IMAGE_FALLBACK_HINT}".strip()
    return result
```

---

## Test Plan

New cases in `tests/master/test_file_converter.py`:

| ID | Input | Expected output |
|---|---|---|
| FC-PDF-04 | PDF with text + embedded image | Extracted text + hint appended |
| FC-PDF-05 | PDF with image only, no text | Hint only (no empty prefix) |
| FC-PDF-06 | PDF with text, no images | Text only, no hint |
| FC-DOCX-05 | docx hint string | Identical to `_IMAGE_FALLBACK_HINT` constant |

---

## Acceptance Criteria

- `_convert_pdf` appends `_IMAGE_FALLBACK_HINT` whenever any page contains images
- `_convert_docx` uses the same constant (no duplicated strings)
- All existing tests pass
- Four new tests added and green
