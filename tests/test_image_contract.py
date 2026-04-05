from __future__ import annotations

from pathlib import Path


def test_agent_minimal_image_copies_config_directory() -> None:
    dockerfile = Path("Dockerfile.agent-minimal").read_text(encoding="utf-8")
    assert "COPY --chown=appuser:appuser config ./config" in dockerfile


def test_entrypoint_has_baked_in_codex_and_claude_global_fallbacks() -> None:
    entrypoint = Path("docker/entrypoint.sh").read_text(encoding="utf-8")
    assert "set -x" in entrypoint
    assert "/run/secrets/master_codex_config" in entrypoint
    assert "/opt/codex-slack/config/codex-global" in entrypoint
    assert "/run/secrets/master_claude_config" in entrypoint
    assert "/opt/codex-slack/config/claude-global" in entrypoint
    assert 'cp -af /run/secrets/master_codex_config/. "${CODEX_HOME_PATH}/"' in entrypoint
    assert 'cp -af /opt/codex-slack/config/codex-global/. "${CODEX_HOME_PATH}/"' in entrypoint
    assert 'entrypoint_log "startup ' in entrypoint
    assert 'entrypoint_log "codex_config_result ' in entrypoint
    assert 'entrypoint_log "claude_config_result ' in entrypoint
