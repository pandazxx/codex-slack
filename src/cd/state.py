from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger(__name__)


@dataclass
class CdState:
    # Full repo-digest reference of the currently running image,
    # e.g. "ghcr.io/org/image@sha256:abc...". None before first deploy.
    deployed_digest: str | None = None
    # Digest of the image that was running before the last deploy — used for rollback.
    previous_digest: str | None = None
    # ISO-8601 UTC timestamp of the last successful deploy.
    deployed_at: str | None = None
    # How many consecutive failed deployments have occurred (reset to 0 on success).
    consecutive_failures: int = 0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: str) -> CdState:
    state_path = Path(path)
    if not state_path.exists():
        return CdState()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        return CdState(
            deployed_digest=raw.get("deployed_digest") or None,
            previous_digest=raw.get("previous_digest") or None,
            deployed_at=raw.get("deployed_at") or None,
            consecutive_failures=int(raw.get("consecutive_failures", 0)),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("cd.state_load_failed path=%s error=%s", path, exc)
        return CdState()


def save_state(path: str, state: CdState) -> None:
    state_path = Path(path)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("cd.state_save_failed path=%s error=%s", path, exc)


def make_deployed_state(new_digest: str, previous_digest: str | None) -> CdState:
    return CdState(
        deployed_digest=new_digest,
        previous_digest=previous_digest,
        deployed_at=_utcnow(),
        consecutive_failures=0,
    )


def make_failed_state(state: CdState) -> CdState:
    return CdState(
        deployed_digest=state.deployed_digest,
        previous_digest=state.previous_digest,
        deployed_at=state.deployed_at,
        consecutive_failures=state.consecutive_failures + 1,
    )
