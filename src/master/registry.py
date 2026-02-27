from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    resolved_image: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentRegistry:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _read_data(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"agents": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if "agents" not in raw or not isinstance(raw["agents"], dict):
            raise ValueError(f"Invalid registry format in {self._path}")
        return raw

    def _write_data(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def list_agents(self) -> list[AgentRecord]:
        data = self._read_data()
        return [AgentRecord(**item) for item in data["agents"].values()]

    def get(self, name: str) -> AgentRecord | None:
        data = self._read_data()
        item = data["agents"].get(name)
        return AgentRecord(**item) if item else None

    def upsert(self, record: AgentRecord) -> AgentRecord:
        data = self._read_data()
        now = utc_now_iso()
        existing = data["agents"].get(record.name)
        if existing and not record.created_at:
            record.created_at = existing.get("created_at", now)
        if not record.created_at:
            record.created_at = now
        record.updated_at = now
        data["agents"][record.name] = record.to_dict()
        self._write_data(data)
        return record

    def remove(self, name: str) -> bool:
        data = self._read_data()
        if name not in data["agents"]:
            return False
        del data["agents"][name]
        self._write_data(data)
        return True

    def find_by_channel(self, channel_id: str) -> AgentRecord | None:
        for record in self.list_agents():
            if record.channel_id == channel_id:
                return record
        return None
