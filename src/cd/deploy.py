from __future__ import annotations

import logging
import os
import subprocess
import time

LOGGER = logging.getLogger(__name__)

# Container statuses considered healthy.
_RUNNING_STATUSES = {"running"}


def _run(
    cmd: list[str],
    *,
    check: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(cmd, text=True, capture_output=True, check=False, env=env)


def get_image_repo_digest(image_ref: str) -> str | None:
    """Return the local repo-digest string for *image_ref* (image@sha256:...).

    The repo-digest is stable and can be used to re-pull the exact same layer
    set later (for rollback).  Returns None when the image is not locally present.
    """
    completed = _run(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image_ref]
    )
    if completed.returncode != 0:
        return None
    digest = completed.stdout.strip()
    return digest if digest else None


def pull_image(image_ref: str) -> str | None:
    """Pull *image_ref* from the registry.

    Returns the repo-digest of the pulled image, or None on failure.
    """
    LOGGER.info("cd.pull_start image=%s", image_ref)
    completed = _run(["docker", "pull", image_ref])
    if completed.returncode != 0:
        LOGGER.warning(
            "cd.pull_failed image=%s\nSTDERR:\n%s",
            image_ref,
            completed.stderr.strip(),
        )
        return None
    digest = get_image_repo_digest(image_ref)
    LOGGER.info("cd.pull_done image=%s digest=%s", image_ref, digest or "-")
    return digest


def get_container_status(container_name: str) -> str | None:
    """Return the Podman status string for *container_name* (e.g. "running", "exited").

    Returns None when the container does not exist.
    """
    completed = _run(
        ["docker", "inspect", "--type", "container", "--format", "{{.State.Status}}", container_name]
    )
    if completed.returncode != 0:
        return None
    status = completed.stdout.strip()
    return status if status else None


def deploy_image(
    *,
    image_ref: str,
    compose_binary: str,
    compose_file: str,
    compose_service: str,
    env_file: str | None,
    dry_run: bool,
) -> bool:
    """Bring up *compose_service* using *image_ref* via compose.

    Sets MASTER_RUNTIME_IMAGE in the subprocess environment so the compose
    file's ``${MASTER_RUNTIME_IMAGE}`` variable resolves to *image_ref*.
    Returns True on success.
    """
    if dry_run:
        LOGGER.info("cd.deploy_dry_run image=%s", image_ref)
        return True

    base_cmd = [*compose_binary.split(), "-f", compose_file]
    if env_file:
        if os.path.isfile(env_file):
            base_cmd.extend(["--env-file", env_file])
        else:
            LOGGER.warning("cd.env_file_missing path=%s skipping_--env-file", env_file)
    extra_env = {"MASTER_RUNTIME_IMAGE": image_ref}

    # Stop then remove before recreating — avoids a name conflict when
    # container_name is set and --force-recreate tries to create the new
    # container before the old one has released its name.
    _run([*base_cmd, "stop", compose_service], extra_env=extra_env)
    _run([*base_cmd, "rm", "-f", compose_service], extra_env=extra_env)

    cmd = [*base_cmd, "up", "-d", "--no-build", compose_service]
    LOGGER.info("cd.deploy_start image=%s cmd=%r", image_ref, cmd)
    completed = _run(cmd, extra_env=extra_env)
    if completed.returncode != 0:
        LOGGER.warning(
            "cd.deploy_failed image=%s\nSTDERR:\n%s\nSTDOUT:\n%s",
            image_ref,
            completed.stderr.strip(),
            completed.stdout.strip(),
        )
        return False
    LOGGER.info("cd.deploy_done image=%s", image_ref)
    return True


def force_recreate_container(
    *,
    image_ref: str,
    compose_binary: str,
    compose_file: str,
    compose_service: str,
    env_file: str | None,
    dry_run: bool,
) -> bool:
    """Tear down and re-create *compose_service* using the currently deployed *image_ref*.

    Unlike restart_container (which does ``docker restart`` in-place), this runs
    ``compose up --force-recreate`` so updated env files and volume mounts take effect.
    Returns True on success.
    """
    LOGGER.info("cd.force_recreate_start image=%s", image_ref)
    ok = deploy_image(
        image_ref=image_ref,
        compose_binary=compose_binary,
        compose_file=compose_file,
        compose_service=compose_service,
        env_file=env_file,
        dry_run=dry_run,
    )
    if ok:
        LOGGER.info("cd.force_recreate_done image=%s", image_ref)
    else:
        LOGGER.error("cd.force_recreate_failed image=%s", image_ref)
    return ok


def restart_container(container_name: str, *, dry_run: bool) -> bool:
    """Restart *container_name* in-place (same image, same config).

    Used on CD daemon startup to ensure the master picks up the latest env.
    Returns True on success or when the container does not exist (nothing to restart).
    """
    if dry_run:
        LOGGER.info("cd.restart_dry_run container=%s", container_name)
        return True

    status = get_container_status(container_name)
    if status is None:
        LOGGER.info("cd.restart_skipped container=%s reason=not_found", container_name)
        return True

    LOGGER.info("cd.restart_start container=%s current_status=%s", container_name, status)
    completed = _run(["docker", "restart", container_name])
    if completed.returncode != 0:
        LOGGER.error(
            "cd.restart_failed container=%s\nSTDERR:\n%s",
            container_name,
            completed.stderr.strip(),
        )
        return False
    LOGGER.info("cd.restart_done container=%s", container_name)
    return True


def check_health(container_name: str, delay_seconds: int) -> bool:
    """Wait *delay_seconds* then check the container is still running.

    This works for Socket Mode services that expose no HTTP port: if the
    process crashes on startup the container transitions to "exited" within
    the delay window.
    """
    LOGGER.info("cd.health_wait container=%s delay=%ds", container_name, delay_seconds)
    time.sleep(delay_seconds)
    status = get_container_status(container_name)
    healthy = status in _RUNNING_STATUSES
    LOGGER.info("cd.health_result container=%s status=%s healthy=%s", container_name, status, healthy)
    return healthy


def rollback_image(
    *,
    image_ref: str,
    compose_binary: str,
    compose_file: str,
    compose_service: str,
    env_file: str | None,
    dry_run: bool,
) -> bool:
    """Re-deploy using the previous image digest to undo a bad release.

    Pulls *image_ref* first (in case it was pruned) then delegates to
    deploy_image.  Returns True on success.
    """
    LOGGER.warning("cd.rollback_start image=%s", image_ref)
    if not dry_run:
        # Ensure the previous image is locally available (it may have been
        # replaced during the failed deploy).
        completed = _run(["docker", "pull", image_ref])
        if completed.returncode != 0:
            LOGGER.error(
                "cd.rollback_pull_failed image=%s\nSTDERR:\n%s",
                image_ref,
                completed.stderr.strip(),
            )
            return False

    ok = deploy_image(
        image_ref=image_ref,
        compose_binary=compose_binary,
        compose_file=compose_file,
        compose_service=compose_service,
        env_file=env_file,
        dry_run=dry_run,
    )
    if ok:
        LOGGER.warning("cd.rollback_done image=%s", image_ref)
    else:
        LOGGER.error("cd.rollback_failed image=%s", image_ref)
    return ok
