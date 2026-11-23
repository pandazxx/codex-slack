"""Mr-deed adapter: runs one topic turn of an in-process mr-deed agent (ADR-0018).

Only mr-deed's public library API is used. `mr_deed` is imported lazily so
workers that never serve a mr-deed staff don't pay for importing litellm, and
`mr_deed.run` (the CLI package, which loads .env files) is never imported.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

LOGGER = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "mr-deed"
# On the per-workspace codex-mrdeed-<workspace_id> volume (agent_runner.spawn_agent).
SESSIONS_DIR = Path("/home/appuser/.mr-deed/sessions")
DEFAULT_CONFIG_NAME = "default"
# Template variable carrying the staff system prompt. Passing the prompt as a
# variable (not spliced into the template) means jinja never interprets it.
SYSTEM_PROMPT_VAR = "codex_slack_system_prompt"

# on_event kinds forwarded to the UI; system/user are the prompt itself.
_PUBLISHED_KINDS = {"assistant", "action", "observation", "format_error", "exit"}

_running_agents: dict[str, Any] = {}
_pending_cancels: dict[str, float] = {}
_registry_lock = threading.Lock()
_PENDING_CANCEL_TTL_SECONDS = 300.0


class ConfigError(Exception):
    """The mr-deed config for a turn could not be resolved or loaded."""


def _import_mr_deed() -> None:
    # litellm (imported by mr-deed's model classes) calls dotenv.load_dotenv()
    # at import time unless LITELLM_MODE is set; outside its proxy, that flag
    # gates nothing else.
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
    import mr_deed.agents.default  # noqa: F401
    import mr_deed.environments.local  # noqa: F401
    import mr_deed.models  # noqa: F401


# ── Config ───────────────────────────────────────────────────────────────────

def resolve_config(agent_config: str | None, name: str | None, config_dir: Path = CONFIG_DIR) -> tuple[dict, str]:
    """Return (config, source). First match wins: the staff's `agent_config`,
    then a shipped `config/mr-deed/<name>.yaml`, then a mr-deed built-in."""
    if agent_config and agent_config.strip():
        return _load_mapping(agent_config, "staff agent_config"), "staff agent_config"
    name = name or DEFAULT_CONFIG_NAME
    if "/" in name or "\\" in name or name.startswith("."):
        raise ConfigError(f"invalid config name '{name}'")
    from mr_deed.config import builtin_config_dir
    for path in (config_dir / f"{name}.yaml", builtin_config_dir / f"{name}.yaml",
                 builtin_config_dir / "extra" / f"{name}.yaml"):
        if path.is_file():
            return _load_mapping(path.read_text(encoding="utf-8"), str(path)), str(path)
    raise ConfigError(f"no mr-deed config named '{name}'")


def _load_mapping(text: str, source: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{source} must be a YAML mapping")
    return data


def apply_overrides(config: dict, model: str | None, system_prompt: str | None) -> dict:
    """Apply the staff's model and system prompt on top of a resolved config."""
    config = copy.deepcopy(config)
    if model:
        config.setdefault("model", {})["model_name"] = model
    if system_prompt:
        agent = config.setdefault("agent", {})
        agent["system_template"] = (
            agent.get("system_template", "").rstrip("\n") + "\n\n{{ " + SYSTEM_PROMPT_VAR + " }}\n"
        )
    return config


def build_environment(config: dict, worktree: str, turn_env: dict[str, str]) -> Any:
    """Always a LocalEnvironment in the worktree; a config cannot pick another type."""
    from mr_deed.environments.local import LocalEnvironment
    env_config = config.get("environment") or {}
    return LocalEnvironment(
        cwd=worktree,
        env={**(env_config.get("env") or {}), **turn_env},
        timeout=env_config.get("timeout", 30),
    )


# ── Running-agent registry (cancel + liveness) ───────────────────────────────

def is_running(reply_message_id: str) -> bool:
    with _registry_lock:
        return reply_message_id in _running_agents


def cancel(reply_message_id: str) -> bool:
    """Cancel a running turn. A cancel for a turn that hasn't registered yet is
    kept briefly and applied on registration. Returns True if a run was signalled."""
    with _registry_lock:
        agent = _running_agents.get(reply_message_id)
        if agent is None:
            _prune_pending_cancels_locked()
            _pending_cancels[reply_message_id] = time.monotonic()
            return False
    agent.cancel()
    return True


def _register(reply_message_id: str, agent: Any) -> None:
    with _registry_lock:
        _running_agents[reply_message_id] = agent
        cancelled_early = _pending_cancels.pop(reply_message_id, None) is not None
    if cancelled_early:
        agent.cancel()


def _unregister(reply_message_id: str) -> None:
    with _registry_lock:
        _running_agents.pop(reply_message_id, None)


def _prune_pending_cancels_locked() -> None:
    cutoff = time.monotonic() - _PENDING_CANCEL_TTL_SECONDS
    for key in [k for k, t in _pending_cancels.items() if t < cutoff]:
        del _pending_cancels[key]


# ── Turn ─────────────────────────────────────────────────────────────────────

def session_path(session_id: str, sessions_dir: Path = SESSIONS_DIR) -> Path:
    return sessions_dir / f"{session_id}.traj.json"


def _slim(payload: dict) -> dict:
    """Drop the raw provider response from a message before it goes on the wire."""
    extra = payload.get("extra")
    if isinstance(extra, dict) and "response" in extra:
        payload = {**payload, "extra": {k: v for k, v in extra.items() if k != "response"}}
    return payload


def run_turn(
    *,
    text: str,
    worktree: str,
    reply_message_id: str,
    session_id: str | None,
    is_new_session: bool,
    session_scope: str,
    config_name: str | None,
    agent_config: str | None,
    model: str | None,
    system_prompt: str | None,
    turn_env: dict[str, str],
    publish_event: Callable[[dict], None],
    sessions_dir: Path = SESSIONS_DIR,
    config_dir: Path = CONFIG_DIR,
    model_factory: Callable[[dict], Any] | None = None,
) -> tuple[str, str | None, str | None]:
    """Run one turn. Returns (reply_text, session_id, transcript_json); never raises."""
    events: list[dict] = []

    def on_event(kind: str, payload: dict) -> None:
        if kind not in _PUBLISHED_KINDS:
            return
        event = {"type": f"mr_deed.{kind}", "message": _slim(payload)}
        events.append(event)
        try:
            publish_event(event)
        except Exception:
            LOGGER.exception("agent.mr_deed_publish_failed message_id=%s kind=%s", reply_message_id, kind)

    def transcript() -> str | None:
        return json.dumps(events, default=str) if events else None

    source = config_name or DEFAULT_CONFIG_NAME
    try:
        _import_mr_deed()
        from mr_deed.agents.default import DefaultAgent
        from mr_deed.models import get_model

        config, source = resolve_config(agent_config, config_name, config_dir)
        config = apply_overrides(config, model, system_prompt)
        env = build_environment(config, worktree, turn_env)
        llm = (model_factory or (lambda cfg: get_model(config=cfg)))(config.get("model") or {})
    except Exception as exc:
        LOGGER.warning("agent.mr_deed_config_invalid message_id=%s source=%s error=%s", reply_message_id, source, exc)
        return f"(mr-deed config '{source}' is invalid: {exc})", None, None

    persisted = bool(session_id) and session_scope != "none"
    path = session_path(session_id, sessions_dir) if persisted else None
    resume = persisted and not is_new_session and path.exists()
    agent_kwargs = {**(config.get("agent") or {}), "output_path": path}
    try:
        if resume:
            agent = DefaultAgent.from_trajectory(path, llm, env, on_event=on_event, **agent_kwargs)
        else:
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            agent = DefaultAgent(llm, env, on_event=on_event, **agent_kwargs)
    except OSError as exc:
        LOGGER.warning("agent.mr_deed_session_dir_unwritable path=%s error=%s", path, exc)
        return f"(mr-deed cannot use its session directory {path.parent}: {exc})", None, None
    except Exception as exc:
        if resume:
            LOGGER.warning("agent.mr_deed_session_unreadable path=%s error=%s", path, exc)
            return f"(mr-deed session file {path} could not be loaded and was left untouched: {exc})", None, None
        LOGGER.warning("agent.mr_deed_config_invalid message_id=%s source=%s error=%s", reply_message_id, source, exc)
        return f"(mr-deed config '{source}' is invalid: {exc})", None, None

    _register(reply_message_id, agent)
    try:
        template_vars = {SYSTEM_PROMPT_VAR: system_prompt or ""}
        result = agent.continue_run(text, **template_vars) if resume else agent.run(text, **template_vars)
    except Exception as exc:
        LOGGER.exception("agent.mr_deed_run_failed message_id=%s", reply_message_id)
        return _exception_reply(exc), session_id if persisted else None, transcript()
    finally:
        _unregister(reply_message_id)

    return _exit_reply(result, agent), session_id if persisted else None, transcript()


def _exception_reply(exc: Exception) -> str:
    if type(exc).__module__.startswith("litellm"):
        return f"(mr-deed model call failed: {type(exc).__name__}: {exc})"
    return f"(mr-deed crashed: {type(exc).__name__})"


def _exit_reply(result: dict, agent: Any) -> str:
    status = result.get("exit_status", "")
    if status == "Submitted":
        return (result.get("submission") or "").strip() or "(no output)"
    if status == "Cancelled":
        return "(cancelled by user)"
    if status == "TimeExceeded":
        return f"(mr-deed timed out after {agent.config.wall_time_limit_seconds}s)"
    if status == "LimitsExceeded":
        return (
            f"(mr-deed hit a limit: step_limit={agent.config.step_limit},"
            f" cost_limit={agent.config.cost_limit}, steps={agent.n_calls}, cost=${agent.cost:.4f})"
        )
    if status == "RepeatedFormatError":
        return "(mr-deed could not produce a parseable action)"
    return f"(mr-deed exited: {status or 'unknown'})"
