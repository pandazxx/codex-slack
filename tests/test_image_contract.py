from __future__ import annotations

from pathlib import Path


def test_agent_minimal_image_copies_config_directory() -> None:
    dockerfile = Path("Dockerfile.agent-minimal").read_text(encoding="utf-8")
    assert "COPY --chown=appuser:appuser config ./config" in dockerfile
