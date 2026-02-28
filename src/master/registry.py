from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRecord:
    name: str
    repo_path: str
    channel_id: str
    container_name: str
    runtime: str
    image_plan: dict[str, Any]
    status: str
    repo_source: str = ""
    repo_ref: str = "main"
    resolved_image: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentRegistry:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._process_lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _file_lock(self) -> Any:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._process_lock, self._lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_data_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"agents": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if "agents" not in raw or not isinstance(raw["agents"], dict):
            raise ValueError(f"Invalid registry format in {self._path}")
        return raw

    def _write_data_unlocked(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def list_agents(self) -> list[AgentRecord]:
        with self._file_lock():
            data = self._read_data_unlocked()
        return [AgentRecord(**item) for item in data["agents"].values()]

    def get(self, name: str) -> AgentRecord | None:
        with self._file_lock():
            data = self._read_data_unlocked()
        item = data["agents"].get(name)
        return AgentRecord(**item) if item else None

    def upsert(self, record: AgentRecord) -> AgentRecord:
        with self._file_lock():
            data = self._read_data_unlocked()
            now = utc_now_iso()
            existing = data["agents"].get(record.name)
            if existing and not record.created_at:
                record.created_at = existing.get("created_at", now)
            if not record.created_at:
                record.created_at = now
            record.updated_at = now
            data["agents"][record.name] = record.to_dict()
            self._write_data_unlocked(data)
        return record

    def remove(self, name: str) -> bool:
        with self._file_lock():
            data = self._read_data_unlocked()
            if name not in data["agents"]:
                return False
            del data["agents"][name]
            self._write_data_unlocked(data)
            return True

    def find_by_channel(self, channel_id: str) -> AgentRecord | None:
        for record in self.list_agents():
            if record.channel_id == channel_id:
                return record
        return None
