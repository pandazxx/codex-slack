from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib import error as url_error
from urllib import request

if TYPE_CHECKING:
    from .config import MasterSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationContent:
    workspace_name: str
    topic_subject: str
    topic_url: str | None  # None if MASTER_PUBLIC_URL unset
    preview: str  # already trimmed, may be ""


def build_topic_url(public_url: str | None, workspace_id: str, topic_id: str) -> str | None:
    """Return the deep-link URL for a topic, or None if public_url is absent or blank."""
    if not public_url or not public_url.strip():
        return None
    base = public_url.rstrip("/")
    return f"{base}/workspaces/{workspace_id}/topics/{topic_id}"


def _render_body(content: NotificationContent) -> str:
    """Render the notification text shared by all channel adapters."""
    lines: list[str] = [f"Agent replied in {content.workspace_name} / {content.topic_subject}"]
    if content.preview:
        lines.append("")
        lines.append(content.preview)
    if content.topic_url is not None:
        lines.append("")
        lines.append(content.topic_url)
    return "\n".join(lines)


def _discord_payload(content: NotificationContent) -> dict:  # type: ignore[type-arg]
    return {"content": _render_body(content)}


def _telegram_payload(content: NotificationContent, chat_id: str) -> dict:  # type: ignore[type-arg]
    return {
        "chat_id": chat_id,
        "text": _render_body(content),
        "disable_web_page_preview": True,
    }


def post_webhook(url: str, payload: dict, dry_run: bool = False) -> None:  # type: ignore[type-arg]
    """POST *payload* as JSON to *url* with a 10-second timeout.

    If dry_run is True the payload is only logged. Errors are logged and
    swallowed so the caller is never interrupted by delivery failures.
    """
    if dry_run:
        logger.info("master.notify.dry_run url=%s message=%r", url, payload)
        return

    data = json.dumps(payload).encode()
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "codex-slack-master/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10):
            pass
    except url_error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        logger.warning("master.notify.failed url=%s status=%s body=%s", url, exc.code, body)
    except (url_error.URLError, OSError) as exc:
        logger.warning("master.notify.failed url=%s error=%s", url, exc)


def notify_reply(
    db_path: str,
    settings: MasterSettings,
    topic_id: str,
    payload: dict,  # type: ignore[type-arg]
) -> None:
    """Fire-and-forget notification when an agent finishes a reply.

    Resolves effective notification config by merging global settings with
    workspace-level config rows (workspace wins). Spawns one daemon thread
    per configured channel; never joins them.
    """
    try:
        row = _fetch_topic_workspace(db_path, topic_id)
    except Exception:
        logger.exception("master.notify.db_error topic_id=%s", topic_id)
        return

    if row is None:
        logger.info("master.notify.skipped reason=topic_not_found topic_id=%s", topic_id)
        return

    workspace_id, workspace_name, topic_subject = row

    try:
        effective = _resolve_effective_config(db_path, workspace_id, settings)
    except Exception:
        logger.exception("master.notify.config_error workspace_id=%s", workspace_id)
        return

    if _is_truthy(effective.get("NOTIFY_DISABLED", "")):
        logger.info(
            "master.notify.skipped reason=disabled workspace_id=%s topic_id=%s",
            workspace_id,
            topic_id,
        )
        return

    preview_chars = int(effective.get("NOTIFY_PREVIEW_CHARS", settings.notify_preview_chars))
    raw_preview = payload.get("last_response", "") or ""
    preview = _trim_preview(raw_preview, preview_chars)

    public_url = effective.get("MASTER_PUBLIC_URL") or settings.master_public_url
    content = NotificationContent(
        workspace_name=workspace_name,
        topic_subject=topic_subject,
        topic_url=build_topic_url(public_url, workspace_id, topic_id),
        preview=preview,
    )

    discord_url = effective.get("NOTIFY_DISCORD_WEBHOOK_URL", "")
    if discord_url:
        threading.Thread(
            target=post_webhook,
            args=(discord_url, _discord_payload(content), settings.dry_run),
            daemon=True,
        ).start()

    telegram_token = effective.get("NOTIFY_TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = effective.get("NOTIFY_TELEGRAM_CHAT_ID", "")
    if telegram_token and telegram_chat_id:
        telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        threading.Thread(
            target=post_webhook,
            args=(telegram_url, _telegram_payload(content, telegram_chat_id), settings.dry_run),
            daemon=True,
        ).start()


def _fetch_topic_workspace(
    db_path: str, topic_id: str
) -> tuple[str, str, str] | None:
    """Return (workspace_id, workspace_name, topic_subject) or None if not found."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT w.id, w.name, t.subject"
            " FROM topics t JOIN workspaces w ON t.workspace_id = w.id"
            " WHERE t.id = ?",
            (topic_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["id"]), str(row["name"]), str(row["subject"])
    finally:
        conn.close()


def _resolve_effective_config(
    db_path: str, workspace_id: str, settings: MasterSettings
) -> dict[str, str]:
    """Merge global env defaults with workspace config rows; workspace wins."""
    global_defaults: dict[str, str] = {}
    if settings.notify_discord_webhook_url:
        global_defaults["NOTIFY_DISCORD_WEBHOOK_URL"] = settings.notify_discord_webhook_url
    if settings.notify_telegram_bot_token:
        global_defaults["NOTIFY_TELEGRAM_BOT_TOKEN"] = settings.notify_telegram_bot_token
    if settings.notify_telegram_chat_id:
        global_defaults["NOTIFY_TELEGRAM_CHAT_ID"] = settings.notify_telegram_chat_id

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT key, value, scope_type FROM config"
            " WHERE (scope_type='global' OR (scope_type='workspace' AND scope_id=?))"
            " ORDER BY scope_type",
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()

    # ORDER BY scope_type: 'global' < 'workspace' alphabetically, so workspace rows
    # are processed last and overwrite global rows — workspace wins.
    workspace_config: dict[str, str] = {}
    global_config_rows: dict[str, str] = {}
    for r in rows:
        if r["scope_type"] == "global":
            global_config_rows[r["key"]] = r["value"]
        else:
            workspace_config[r["key"]] = r["value"]

    effective = {**global_defaults, **global_config_rows, **workspace_config}
    return effective


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _trim_preview(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"
