"""Slack outbound file upload helper.

Exposes:
  - upload_result_files_to_slack: upload a list of file paths to a Slack channel
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def upload_result_files_to_slack(
    *,
    slack_client: Any,
    channel_id: str,
    file_paths: list[Path],
    thread_ts: str | None = None,
) -> None:
    """Upload output files to a Slack channel via the files.upload v2 API.

    An empty *file_paths* list is a no-op.  Upload errors are absorbed and
    logged; they do not propagate to the caller.
    """
    for path in file_paths:
        try:
            slack_client.files_upload_v2(
                channel=channel_id,
                file=str(path),
                filename=path.name,
                thread_ts=thread_ts,
            )
            LOGGER.info("slack_uploader.file_uploaded filename=%s channel=%s", path.name, channel_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("slack_uploader.file_upload_failed filename=%s error=%s", path.name, exc)
