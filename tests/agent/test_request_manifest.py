from __future__ import annotations

import json

from src.agent.request_manifest import load_request_manifest


def test_load_request_manifest_reads_derived_document_and_images(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "request_id": "discord-1-2",
                "attachments": [
                    {
                        "id": "att-1",
                        "kind": "document",
                        "filename": "example.docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "staged_path": "/workspace/message/discord-1-2/source/example.docx",
                        "format_hint": "docx",
                        "derived": {
                            "markdown_path": "/workspace/message/discord-1-2/derived/att-1/document.md",
                            "assets_dir": "/workspace/message/discord-1-2/derived/att-1/assets",
                            "manifest_path": "/workspace/message/discord-1-2/derived/att-1/derived.json",
                            "converter": "mammoth",
                            "warnings": [],
                        },
                    },
                    {
                        "id": "img-1",
                        "kind": "image",
                        "filename": "diagram.png",
                        "content_type": "image/png",
                        "staged_path": "/workspace/message/discord-1-2/source/diagram.png",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_REQUEST_MANIFEST", str(manifest_path))

    manifest = load_request_manifest()

    assert manifest.request_id == "discord-1-2"
    assert len(manifest.document_attachments) == 1
    assert manifest.document_attachments[0].derived is not None
    assert manifest.document_attachments[0].derived.markdown_path.endswith("/document.md")
    assert len(manifest.image_attachments) == 1
