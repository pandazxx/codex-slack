from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import zipfile
from xml.etree import ElementTree as ET


DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CONTENT_TYPE = "application/pdf"

WORD_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


@dataclass(frozen=True)
class ConversionResult:
    converter: str
    markdown_path: Path
    assets_dir: Path
    manifest_path: Path
    warnings: list[str]


def convert_document_to_markdown(*, source_path: Path, output_dir: Path) -> ConversionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "document.md"
    manifest_path = output_dir / "derived.json"

    suffix = source_path.suffix.lower()
    if suffix == ".docx":
        converter, markdown_text, warnings, asset_names = _convert_docx(source_path=source_path, assets_dir=assets_dir)
    elif suffix == ".pdf":
        converter, markdown_text, warnings, asset_names = _convert_pdf(source_path=source_path, assets_dir=assets_dir)
    else:
        raise ValueError(f"unsupported document format: {source_path.suffix}")

    markdown_path.write_text(markdown_text.rstrip() + "\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "attachment_id": output_dir.name,
                "source_path": str(source_path),
                "format": suffix.lstrip("."),
                "converter": converter,
                "derived_markdown_path": str(markdown_path),
                "assets_dir": str(assets_dir),
                "assets": [f"assets/{name}" for name in asset_names],
                "warnings": warnings,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ConversionResult(
        converter=converter,
        markdown_path=markdown_path,
        assets_dir=assets_dir,
        manifest_path=manifest_path,
        warnings=warnings,
    )


def _convert_docx(*, source_path: Path, assets_dir: Path) -> tuple[str, str, list[str], list[str]]:
    warnings: list[str] = []
    asset_names: list[str] = []
    lines: list[str] = []

    with zipfile.ZipFile(source_path) as archive:
        rels = _load_docx_relationships(archive)
        root = ET.fromstring(archive.read("word/document.xml"))  # noqa: S314
        body = root.find("w:body", WORD_NS)
        if body is None:
            raise ValueError("docx document.xml missing body")

        image_counter = 0
        for child in body:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "p":
                paragraph_lines, image_counter, paragraph_assets = _docx_paragraph_to_markdown(
                    archive=archive,
                    paragraph=child,
                    rels=rels,
                    assets_dir=assets_dir,
                    image_counter=image_counter,
                )
                if paragraph_lines:
                    lines.extend(paragraph_lines)
                asset_names.extend(paragraph_assets)
                continue
            if local_name == "tbl":
                table_lines = _docx_table_to_markdown(child)
                if table_lines:
                    lines.extend(table_lines)

    if not lines:
        lines.append(f"# {source_path.name}")
        warnings.append("No readable text extracted from DOCX")

    return "docx-xml-fallback", "\n\n".join(line for line in lines if line.strip()), warnings, asset_names


def _load_docx_relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        raw = archive.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = ET.fromstring(raw)  # noqa: S314
    rels: dict[str, str] = {}
    for rel in root.findall("rel:Relationship", WORD_NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = target
    return rels


def _docx_paragraph_to_markdown(
    *,
    archive: zipfile.ZipFile,
    paragraph: ET.Element,
    rels: dict[str, str],
    assets_dir: Path,
    image_counter: int,
) -> tuple[list[str], int, list[str]]:
    text_parts = [node.text for node in paragraph.findall(".//w:t", WORD_NS) if node.text]
    text = "".join(text_parts).strip()
    lines: list[str] = []
    asset_names: list[str] = []

    style_val = None
    style_node = paragraph.find("w:pPr/w:pStyle", WORD_NS)
    if style_node is not None:
        style_val = style_node.attrib.get(f"{{{WORD_NS['w']}}}val", "")
    if text:
        if style_val and style_val.lower().startswith("heading"):
            level = style_val[len("Heading"):] if style_val.startswith("Heading") else "1"
            try:
                heading_level = max(1, min(int(level), 6))
            except ValueError:
                heading_level = 1
            lines.append(f"{'#' * heading_level} {text}")
        else:
            lines.append(text)

    for blip in paragraph.findall(".//a:blip", WORD_NS):
        rel_id = blip.attrib.get(f"{{{WORD_NS['r']}}}embed")
        target = rels.get(rel_id or "")
        if not target:
            continue
        image_counter += 1
        source_name = Path(target).name
        asset_name = f"image-{image_counter:03d}{Path(source_name).suffix or '.bin'}"
        asset_path = assets_dir / asset_name
        with archive.open(f"word/{target}") as src, asset_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        lines.append(f"![{asset_name}](assets/{asset_name})")
        asset_names.append(asset_name)

    return lines, image_counter, asset_names


def _docx_table_to_markdown(table: ET.Element) -> list[str]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", WORD_NS):
        cells: list[str] = []
        for cell in row.findall("w:tc", WORD_NS):
            cell_text = "".join(node.text or "" for node in cell.findall(".//w:t", WORD_NS)).strip()
            cells.append(cell_text)
        if cells:
            rows.append(cells)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(normalized[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in normalized[1:]]
    return [header, separator, *body]


def _convert_pdf(*, source_path: Path, assets_dir: Path) -> tuple[str, str, list[str], list[str]]:
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001
        return (
            "pdf-fallback",
            f"# {source_path.name}\n\nPDF conversion requires the optional `pypdf` dependency.",
            ["PDF text extraction unavailable because pypdf is not installed"],
            [],
        )

    reader = PdfReader(str(source_path))
    lines: list[str] = [f"# {source_path.name}"]
    warnings: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            warnings.append(f"Page {idx} has no extractable text")
            continue
        lines.append(f"## Page {idx}\n\n{text}")
    if len(lines) == 1:
        warnings.append("No extractable text found in PDF")
    return "pypdf", "\n\n".join(lines), warnings, []
