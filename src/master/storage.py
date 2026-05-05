from __future__ import annotations
import uuid
from pathlib import Path
from typing import Protocol


class AttachmentStore(Protocol):
    def put(self, attachment_id: str, filename: str, data: bytes) -> str: ...
    def get(self, storage_uri: str) -> bytes: ...
    def delete(self, storage_uri: str) -> None: ...


class LocalAttachmentStore:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)

    def put(self, attachment_id: str, filename: str, data: bytes) -> str:
        dest = self.base_dir / attachment_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / filename).write_bytes(data)
        return f"file://{dest / filename}"

    def get(self, storage_uri: str) -> bytes:
        path = storage_uri.removeprefix("file://")
        return Path(path).read_bytes()

    def delete(self, storage_uri: str) -> None:
        path = Path(storage_uri.removeprefix("file://"))
        if path.exists():
            path.unlink()
