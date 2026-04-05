from __future__ import annotations

from dataclasses import dataclass

SPLIT_HINT_LINE = "🔹🔹🔹"


@dataclass(frozen=True)
class ReplyDeliveryPlan:
    messages: list[str]
    file_text: str | None = None
    file_name: str = "response.md"

    @property
    def send_as_file(self) -> bool:
        return self.file_text is not None


def split_on_hint_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]

    sections: list[str] = []
    current: list[str] = []
    pending_marker: str | None = None
    found_hint = False

    for line in lines:
        if line.rstrip("\r\n") == SPLIT_HINT_LINE:
            found_hint = True
            if pending_marker is not None:
                current.append(pending_marker)
                sections.append("".join(current).rstrip())
                current = []
            elif current:
                sections.append("".join(current).rstrip())
                current = []
            pending_marker = line
            continue

        if pending_marker is not None:
            current.append(pending_marker)
            pending_marker = None
        current.append(line)

    if pending_marker is not None:
        current.append(pending_marker)
    if current:
        sections.append("".join(current).rstrip())

    if not found_hint:
        return [text]
    return [section for section in sections if section]


def split_by_size(text: str, *, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n").lstrip(" ")
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]
