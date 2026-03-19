from __future__ import annotations

from unittest.mock import patch

import pytest

from src.cd.config import CdSettings
from src.cd.daemon import run_once
from src.cd.state import CdState

_DIGEST_OLD = "ghcr.io/org/image@sha256:old"
_DIGEST_NEW = "ghcr.io/org/image@sha256:new"


def _settings(**kwargs) -> CdSettings:  # type: ignore[no-untyped-def]
    defaults = dict(
        image="ghcr.io/org/image",
        image_tag="latest",
        container_name="codex-slack-master",
        compose_file="docker-compose.master.yml",
        compose_service="codex-slack-master",
        compose_binary="docker compose",
        env_file=None,
        state_file="/tmp/cd-test-state.json",
        poll_interval_seconds=60,
        health_check_delay_seconds=1,
        rollback_on_failure=True,
        dry_run=True,
    )
    defaults.update(kwargs)
    return CdSettings(**defaults)


def _state(**kwargs) -> CdState:  # type: ignore[no-untyped-def]
    defaults = dict(deployed_digest=_DIGEST_OLD, previous_digest=None, deployed_at=None, consecutive_failures=0)
    defaults.update(kwargs)
    return CdState(**defaults)


# ---------------------------------------------------------------------------
# No new image
# ---------------------------------------------------------------------------


def test_run_once_no_change_when_digest_unchanged() -> None:
    state = _state(deployed_digest=_DIGEST_NEW)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW):
        new_state = run_once(_settings(), state)
    assert new_state is state


def test_run_once_no_change_when_pull_fails() -> None:
    state = _state()
    with patch("src.cd.daemon.pull_image", return_value=None):
        new_state = run_once(_settings(), state)
    assert new_state is state


# ---------------------------------------------------------------------------
# Successful deploy
# ---------------------------------------------------------------------------


def test_run_once_deploys_and_returns_updated_state_on_success() -> None:
    state = _state(deployed_digest=_DIGEST_OLD)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=True), \
         patch("src.cd.daemon.check_health", return_value=True):
        new_state = run_once(_settings(), state)
    assert new_state.deployed_digest == _DIGEST_NEW
    assert new_state.previous_digest == _DIGEST_OLD
    assert new_state.consecutive_failures == 0


# ---------------------------------------------------------------------------
# Deploy command fails
# ---------------------------------------------------------------------------


def test_run_once_increments_failures_when_deploy_fails_no_rollback() -> None:
    state = _state(consecutive_failures=0)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=False):
        new_state = run_once(_settings(rollback_on_failure=False), state)
    assert new_state.consecutive_failures == 1
    assert new_state.deployed_digest == _DIGEST_OLD  # unchanged


def test_run_once_attempts_rollback_when_deploy_fails() -> None:
    state = _state(deployed_digest=_DIGEST_OLD)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=False), \
         patch("src.cd.daemon.rollback_image", return_value=True) as mock_rb, \
         patch("src.cd.daemon.check_health", return_value=True):
        new_state = run_once(_settings(rollback_on_failure=True), state)
    mock_rb.assert_called_once()
    assert mock_rb.call_args.kwargs["image_ref"] == _DIGEST_OLD


def test_run_once_skips_rollback_when_no_previous_digest() -> None:
    state = _state(deployed_digest=None, previous_digest=None)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=False), \
         patch("src.cd.daemon.rollback_image") as mock_rb:
        run_once(_settings(rollback_on_failure=True), state)
    mock_rb.assert_not_called()


# ---------------------------------------------------------------------------
# Health check fails after successful deploy
# ---------------------------------------------------------------------------


def test_run_once_rolls_back_when_health_check_fails() -> None:
    state = _state(deployed_digest=_DIGEST_OLD)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=True), \
         patch("src.cd.daemon.check_health", return_value=False), \
         patch("src.cd.daemon.rollback_image", return_value=True) as mock_rb:
        new_state = run_once(_settings(rollback_on_failure=True), state)
    mock_rb.assert_called_once()
    assert mock_rb.call_args.kwargs["image_ref"] == _DIGEST_OLD
    # State reverts to previous good digest.
    assert new_state.deployed_digest == _DIGEST_OLD


def test_run_once_does_not_roll_back_when_disabled() -> None:
    state = _state(deployed_digest=_DIGEST_OLD)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=True), \
         patch("src.cd.daemon.check_health", return_value=False), \
         patch("src.cd.daemon.rollback_image") as mock_rb:
        new_state = run_once(_settings(rollback_on_failure=False), state)
    mock_rb.assert_not_called()
    assert new_state.consecutive_failures == 1


def test_run_once_reports_error_when_rollback_also_unhealthy() -> None:
    state = _state(deployed_digest=_DIGEST_OLD)
    # deploy succeeds, health fails, rollback deploys but rolled-back container also crashes
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=True), \
         patch("src.cd.daemon.check_health", return_value=False), \
         patch("src.cd.daemon.rollback_image", return_value=True):
        # check_health is False for both the new image AND the rollback
        new_state = run_once(_settings(rollback_on_failure=True), state)
    assert new_state.consecutive_failures >= 1


# ---------------------------------------------------------------------------
# First-ever deploy (no previous deployed digest)
# ---------------------------------------------------------------------------


def test_run_once_first_deploy_no_previous() -> None:
    state = _state(deployed_digest=None, previous_digest=None)
    with patch("src.cd.daemon.pull_image", return_value=_DIGEST_NEW), \
         patch("src.cd.daemon.deploy_image", return_value=True), \
         patch("src.cd.daemon.check_health", return_value=True):
        new_state = run_once(_settings(), state)
    assert new_state.deployed_digest == _DIGEST_NEW
    assert new_state.previous_digest is None
