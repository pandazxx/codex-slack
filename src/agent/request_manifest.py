from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class DerivedArtifact:
    markdown_path: str
    assets_dir: str
    manifest_path: str
    converter: str
    warnings: list[str]


@dataclass(frozen=True)
class RequestAttachment:
    id: str
    kind: str
    filename: str
    content_type: str
    staged_path: str
    format_hint: str | None = None
    derived: DerivedArtifact | None = None


@dataclass(frozen=True)
class RequestManifest:
    request_id: str
    attachments: list[RequestAttachment]

    @property
    def document_attachments(self) -> list[RequestAttachment]:
        return [item for item in self.attachments if item.kind == "document"]

    @property
    def image_attachments(self) -> list[RequestAttachment]:
        return [item for item in self.attachments if item.kind == "image"]


def load_request_manifest(path: str | None = None) -> RequestManifest:
    manifest_path = (path or os.getenv("AGENT_REQUEST_MANIFEST", "")).strip()
    if not manifest_path:
        raise ValueError("AGENT_REQUEST_MANIFEST is not set")
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    request_id = str(payload.get("request_id", "")).strip()
    if not request_id:
        raise ValueError("request manifest missing request_id")
    raw_attachments = payload.get("attachments")
    if not isinstance(raw_attachments, list):
        raise ValueError("request manifest missing attachments list")
    attachments: list[RequestAttachment] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        derived = raw.get("derived")
        derived_item = None
        if isinstance(derived, dict):
            derived_item = DerivedArtifact(
                markdown_path=str(derived.get("markdown_path", "")),
                assets_dir=str(derived.get("assets_dir", "")),
                manifest_path=str(derived.get("manifest_path", "")),
                converter=str(derived.get("converter", "")),
                warnings=[str(item) for item in derived.get("warnings", []) if isinstance(item, str)],
            )
        attachments.append(
            RequestAttachment(
                id=str(raw.get("id", "")),
                kind=str(raw.get("kind", "")),
                filename=str(raw.get("filename", "")),
                content_type=str(raw.get("content_type", "")),
                staged_path=str(raw.get("staged_path", "")),
                format_hint=str(raw.get("format_hint")) if raw.get("format_hint") is not None else None,
                derived=derived_item,
            )
        )
    return RequestManifest(request_id=request_id, attachments=attachments)
