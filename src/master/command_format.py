from __future__ import annotations

import json

from .command_dispatch import parse_status_text
from .service import CommandResult


def _clip(value: object, limit: int = 40) -> str:
    text = str(value) if value is not None else "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_agent_list_table(result: CommandResult) -> str | None:
    agents = result.data.get("agents")
    if not isinstance(agents, list):
        return None

    if not agents:
        return ":white_check_mark: *No agents loaded.*"

    lines = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        lines.append(
            (
                f"• *{_clip(item.get('name', '-'), 24)}*"
                f" | state=`{_clip(item.get('status', '-'), 12)}`"
                f" | channel=`{_clip(item.get('channel_id', '-'), 14)}`"
                f" | ref=`{_clip(item.get('repo_ref', '-'), 12)}`"
                f" | runtime=`{_clip(item.get('runtime', '-'), 10)}`"
                f" | container=`{_clip(item.get('container_name', '-'), 28)}`"
            )
        )

    return "\n".join(
        [
            f":white_check_mark: */master-agent-list*",
            f"*Total agents:* {len(agents)}",
            *lines,
        ]
    )


def _format_agent_usage(result: CommandResult) -> str | None:
    usage = result.data.get("usage")
    if not isinstance(usage, list):
        return None
    if not usage:
        return ":white_check_mark: *No usage recorded yet.*"

    lines = [":bar_chart: */master-agent-usage*"]
    for item in usage:
        if not isinstance(item, dict):
            continue
        lines.append(
            (
                f"• *{_clip(item.get('agent_name', '-'), 24)}*"
                f" | prompts=`{item.get('prompt_count', 0)}`"
                f" | images=`{item.get('image_count', 0)}`"
                f" | prompt_chars=`{item.get('prompt_chars', 0)}`"
                f" | response_chars=`{item.get('response_chars', 0)}`"
                f" | avg_ms=`{item.get('avg_latency_ms', 0)}`"
            )
        )
    return "\n".join(lines)


def _chunk_text(text: str, max_chars: int = 2800) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_chars and current:
            chunks.append(current)
            current = ""
        if len(line) > max_chars:
            for i in range(0, len(line), max_chars):
                part = line[i : i + max_chars]
                if len(part) == max_chars:
                    chunks.append(part)
                else:
                    current = part
            continue
        current += line
    if current:
        chunks.append(current)
    return chunks


def format_status_full_chunks(command_name: str, result: CommandResult) -> list[str]:
    payload = {
        "ok": result.ok,
        "command": command_name,
        "code": result.code,
        "message": result.message,
        "data": result.data,
    }
    raw = json.dumps(payload, indent=2, sort_keys=True)
    parts = _chunk_text(raw, max_chars=2800)
    total = len(parts)
    return [
        f":information_source: *{command_name} full output* (part {idx}/{total})\n```json\n{part}\n```"
        for idx, part in enumerate(parts, start=1)
    ]


def _format_status_summary(result: CommandResult) -> str | None:
    if not isinstance(result.data, dict):
        return None
    record = result.data.get("record")
    runtime = result.data.get("runtime")
    if not isinstance(record, dict):
        return None

    runtime_state = "-"
    if isinstance(runtime, dict):
        state = runtime.get("State")
        if isinstance(state, dict):
            runtime_state = str(state.get("Status") or state.get("Running") or "-")

    return "\n".join(
        [
            ":white_check_mark: */master-agent-status*",
            f"*Agent:* `{record.get('name', '-')}`",
            f"*State:* registry=`{record.get('status', '-')}` runtime=`{runtime_state}`",
            f"*Channel:* `{record.get('channel_id', '-')}`",
            f"*Branch:* `{record.get('repo_ref', '-')}`",
            f"*Container:* `{record.get('container_name', '-')}`",
            f"*Image:* `{record.get('resolved_image') or '-'}`",
            f"*Last error:* `{record.get('last_error') or '-'}`",
            "_Use `/master-agent-status <name> --full` for full JSON output._",
        ]
    )


def format_command_result(command_name: str, result: CommandResult) -> str:
    if command_name == "/master-agent-list":
        rendered_table = _format_agent_list_table(result)
        if rendered_table:
            return rendered_table
    if command_name == "/master-agent-usage":
        rendered_usage = _format_agent_usage(result)
        if rendered_usage:
            return rendered_usage
    if command_name == "/master-agent-status":
        rendered_summary = _format_status_summary(result)
        if rendered_summary:
            return rendered_summary

    status_icon = ":white_check_mark:" if result.ok else ":x:"
    lines = [
        f"{status_icon} *{command_name}*",
        f"*Code:* `{result.code}`",
        f"*Message:* {result.message}",
    ]
    if result.data:
        data_json = json.dumps(result.data, indent=2, sort_keys=True)
        if len(data_json) > 6000:
            data_json = data_json[:5997] + "..."
        lines.append("*Data:*")
        lines.append(f"```json\n{data_json}\n```")
    return "\n".join(lines)


def wants_full_status(command_name: str, text: str) -> bool:
    if command_name != "/master-agent-status":
        return False
    try:
        _, is_full = parse_status_text(text, command_name)
        return is_full
    except ValueError:
        return False
