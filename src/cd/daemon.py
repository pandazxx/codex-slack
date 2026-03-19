from __future__ import annotations

import logging
import time

from .config import CdSettings
from .deploy import (
    check_health,
    deploy_image,
    pull_image,
    rollback_image,
)
from .notify import notify
from .state import CdState, load_state, make_deployed_state, make_failed_state, save_state

LOGGER = logging.getLogger(__name__)


def run_once(settings: CdSettings, state: CdState) -> CdState:
    """Check for a new image and deploy it if the digest has changed.

    Returns an updated CdState.  The caller is responsible for persisting it.

    Deploy / rollback flow
    ----------------------
    1. Pull ``<image>:<tag>`` and read its repo-digest.
    2. If the digest matches *state.deployed_digest* → nothing to do.
    3. Otherwise stop the master via compose force-recreate with the new image.
    4. Wait ``health_check_delay_seconds`` then verify the container is running.
    5. If the health check fails and rollback is enabled → re-deploy with
       *state.deployed_digest* (the image that was healthy before this attempt).
    """
    image_ref = f"{settings.image}:{settings.image_tag}"

    new_digest = pull_image(image_ref)
    if new_digest is None:
        LOGGER.warning("cd.poll_skipped image=%s reason=pull_failed", image_ref)
        return state

    if new_digest == state.deployed_digest:
        LOGGER.debug("cd.poll_unchanged image=%s digest=%s", image_ref, new_digest)
        return state

    LOGGER.info(
        "cd.new_image image=%s old_digest=%s new_digest=%s",
        image_ref,
        state.deployed_digest or "-",
        new_digest,
    )

    previous_digest = state.deployed_digest

    deploy_ok = deploy_image(
        image_ref=new_digest,
        compose_binary=settings.compose_binary,
        compose_file=settings.compose_file,
        compose_service=settings.compose_service,
        env_file=settings.env_file,
        dry_run=settings.dry_run,
    )

    if not deploy_ok:
        LOGGER.error("cd.deploy_failed image=%s", new_digest)
        notify(settings, f":x: CD deploy failed for `{image_ref}` — digest `{new_digest}`.")
        failed_state = make_failed_state(state)
        if settings.rollback_on_failure and previous_digest:
            _do_rollback(settings=settings, image_ref=previous_digest, current_state=failed_state)
        return failed_state

    healthy = check_health(settings.container_name, settings.health_check_delay_seconds)

    if healthy:
        new_state = make_deployed_state(new_digest=new_digest, previous_digest=previous_digest)
        LOGGER.info(
            "cd.deploy_success image=%s digest=%s",
            image_ref,
            new_digest,
        )
        notify(settings, f":white_check_mark: CD deployed `{image_ref}` successfully (digest `{new_digest}`).")
        return new_state

    # Health check failed.
    LOGGER.error(
        "cd.health_check_failed image=%s digest=%s",
        image_ref,
        new_digest,
    )
    notify(
        settings,
        f":warning: CD health check failed after deploying `{image_ref}` (digest `{new_digest}`)."
        + (" Rolling back..." if settings.rollback_on_failure and previous_digest else ""),
    )
    failed_state = make_failed_state(state)
    if settings.rollback_on_failure and previous_digest:
        rolled_back = _do_rollback(
            settings=settings,
            image_ref=previous_digest,
            current_state=failed_state,
        )
        if rolled_back:
            notify(
                settings,
                f":arrows_counterclockwise: CD rolled back `{settings.container_name}` to `{previous_digest}`.",
            )
            # Restore state to the last known-good digest.
            return make_deployed_state(
                new_digest=previous_digest,
                previous_digest=previous_digest,
            )
    return failed_state


def _do_rollback(*, settings: CdSettings, image_ref: str, current_state: CdState) -> bool:
    ok = rollback_image(
        image_ref=image_ref,
        compose_binary=settings.compose_binary,
        compose_file=settings.compose_file,
        compose_service=settings.compose_service,
        env_file=settings.env_file,
        dry_run=settings.dry_run,
    )
    if ok:
        # Give the rolled-back container a moment to come up, then verify.
        healthy = check_health(settings.container_name, settings.health_check_delay_seconds)
        if not healthy:
            LOGGER.error(
                "cd.rollback_also_unhealthy image=%s consecutive_failures=%d",
                image_ref,
                current_state.consecutive_failures + 1,
            )
            notify(
                settings,
                f":sos: CD rollback also unhealthy for `{image_ref}` — manual intervention required.",
            )
            return False
    return ok


def run_loop(settings: CdSettings) -> None:
    """Poll for new images in a blocking loop, deploying and rolling back as needed."""
    LOGGER.info(
        "cd.daemon_start image=%s tag=%s container=%s poll_interval=%ds health_delay=%ds rollback=%s dry_run=%s",
        settings.image,
        settings.image_tag,
        settings.container_name,
        settings.poll_interval_seconds,
        settings.health_check_delay_seconds,
        settings.rollback_on_failure,
        settings.dry_run,
    )

    state = load_state(settings.state_file)
    LOGGER.info(
        "cd.daemon_loaded_state deployed_digest=%s previous_digest=%s deployed_at=%s",
        state.deployed_digest or "-",
        state.previous_digest or "-",
        state.deployed_at or "-",
    )

    while True:
        try:
            new_state = run_once(settings, state)
            if new_state is not state:
                state = new_state
                save_state(settings.state_file, state)
        except Exception:  # noqa: BLE001
            LOGGER.exception("cd.loop_error")

        time.sleep(settings.poll_interval_seconds)
