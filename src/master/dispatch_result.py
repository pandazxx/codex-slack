from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DispatchResult:
    text: str
    file_paths: list[Path] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DispatchResult):
            return self.text == other.text and self.file_paths == other.file_paths
        if isinstance(other, str):
            return self.text == other and not self.file_paths
        return NotImplemented

    def __len__(self) -> int:
        return len(self.text)
