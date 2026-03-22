from __future__ import annotations

import datetime
import logging


class LocalTimeFormatter(logging.Formatter):
    """Logging formatter that uses local time (respects TZ env var) with timezone offset."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.datetime.fromtimestamp(record.created).astimezone()
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S %z")
