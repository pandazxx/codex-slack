"""MQTT-driven prompt processor for the v3 agent worker."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)

_TOPIC_PARTS = 6  # codex-slack/workspace/{wid}/topic/{tid}/prompt
_LLM_TIMEOUT = None  # no timeout — claude/codex can run as long as needed

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-llm")

# Tracks active claude subprocesses by reply_message_id so they can be killed on cancel.
_active_procs: dict[str, subprocess.Popen] = {}  # type: ignore[type-arg]
_active_contexts: dict[str, dict] = {}  # message_id → {workspace_id, topic_id, agent_name}
_active_procs_lock = threading.Lock()

# Persisted snapshot of in-flight subprocess contexts so that a SIGKILL-survivor
# restart can emit a real interrupt event for messages whose subprocess died
# along with the container. Path is on the container's writable layer; survives
# a Docker restart (same container) but not a remove+recreate, which is fine —
# remove+recreate is master-initiated and master's stale-stream check already
# detects "container-gone" in that path.
_STATE_DIR = Path("/tmp/master-agent")
_ACTIVE_PROCS_PATH = _STATE_DIR / "active_procs.json"
_inherited_already_published = False

# Deduplication: clean_session=False means the broker redelivers QoS-1 prompts when
# the agent reconnects mid-run, which would start a second parallel LLM run and produce
# two streaming bubbles. Track seen message_ids (bounded to 2000) and discard re-deliveries.
# Accessed only from the single-threaded MQTT event loop — no lock required.
_seen_prompt_ids: set[str] = set()
_seen_prompt_ids_queue: deque[str] = deque()
_MAX_SEEN_PROMPT_IDS = 2000

# Module-level reference to the MQTT client, used by the SIGTERM handler.
_mqtt_client_ref: mqtt.Client | None = None

# Thread-local storage for per-prompt orchestration env vars.  Set in
# _process_prompt before the LLM call; read in _run_claude/_run_codex, which
# then thread the dict explicitly down to _stream_claude_once/_stream_codex_once
# as an orch_env parameter.  The thread-local boundary exists only at the
# _process_prompt → _run_claude/_run_codex gap because those functions are
# mocked in tests with fixed positional signatures — adding orch_env there
# would require updating test mocks.  Everything below _run_*/_run_codex uses
# the explicit parameter.
_prompt_orch_env: threading.local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kill_proc_tree(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Kill a subprocess and all its descendants via process-group signal.

    Requires the proc to have been spawned with start_new_session=True so its
    pgid == pid and killing the group only affects that subprocess tree.
    """
    try:
        pgid = int(proc.pid)
    except (TypeError, ValueError):
        LOGGER.warning("agent.kill_proc_tree_invalid_pid pid=%r", proc.pid)
        return
    # Refuse pgid <= 1. killpg(N, sig) issues kill(-N, sig); kill(-1, sig)
    # broadcasts SIGKILL to every process the caller can signal (which inside
    # a container is the whole namespace). kill(0, sig) targets the caller's
    # own process group. Either is a self-destruct, not a child-subprocess
    # kill. Legitimate subprocess pgids are always >= 2 in a tini-PID-1
    # container. This guard exists because MagicMock().__int__() returns 1,
    # so an under-mocked test that hits this path would kill its own process
    # tree — see docs/knowledge-base/lessons-learned.md (2026-05-18).
    if pgid <= 1:
        LOGGER.warning("agent.kill_proc_tree_refused_unsafe_pgid pgid=%d", pgid)
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _parse_prompt_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "prompt":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _parse_cancel_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "cancel":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _ping_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/ping"


def _pong_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/pong"


def _parse_ping_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "ping":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _status_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/status"


def _response_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/response"


def _chunk_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/chunk"


def _verdict_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/verdict"


def _fetch_remote(repo_dir: str, repo_ref: str) -> None:
    """Refresh remote-tracking refs before branching so the worktree is cut from
    up-to-date remote state.  The agent's clone is only synced once at container
    startup (stage_repo_sync); without this fetch a topic created later branches
    off a stale origin/<repo_ref> and lands far behind remote (issue #250).  It
    also pulls the object referenced by base_sha into the local store when the
    remote has moved forward since startup.

    Best-effort: a fetch failure (offline, transient network) must not block topic
    creation — the worktree is still created from whatever refs are already local.
    """
    cmd = ["git", "-C", repo_dir, "fetch", "origin"]
    if repo_ref:
        cmd.append(repo_ref)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        LOGGER.warning("agent.worktree_fetch_failed repo_ref=%s stderr=%s", repo_ref, result.stderr.strip())
    else:
        LOGGER.info("agent.worktree_fetched repo_ref=%s", repo_ref or "(default refs)")


def _ensure_worktree(repo_dir: str, worktree_path: str, branch: str, repo_ref: str = "", base_sha: str = "") -> None:
    if Path(worktree_path).exists():
        return
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
    # Sync remote refs first so origin/<repo_ref> and the base_sha object are current.
    _fetch_remote(repo_dir, repo_ref)
    # Prefer the pre-resolved SHA — it always exists in the local clone regardless of
    # which branch the agent repo was cloned from.  Fall back to origin/<repo_ref> so
    # remote-tracking refs resolve even when no local branch of that name exists.
    commit_ish = base_sha or (f"origin/{repo_ref}" if repo_ref else "")
    base = [commit_ish] if commit_ish else []
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, "-b", branch] + base,
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_created path=%s branch=%s base=%s", worktree_path, branch, commit_ish or "HEAD")
    except subprocess.CalledProcessError:
        # Branch already exists — check out without -b
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, branch],
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_reused path=%s branch=%s", worktree_path, branch)


_SESSION_NOT_FOUND = "No conversation found with session ID"
_CODEX_SESSION_NOT_FOUND = "no rollout found for thread id"
_MCP_CONFIG = "/opt/codex-slack/config/agent-mcp.json"

_VERDICT_RE = __import__("re").compile(r'\{[^{}]+\}')


def _extract_verdict(response_text: str) -> dict:  # type: ignore[type-arg]
    """Find the last {verdict, reason} JSON object in the LLM response.

    Tries to parse the entire text as JSON first, then scans for the last
    JSON object containing a valid verdict key. Falls back to allow if none found.
    """
    text = response_text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("verdict") in ("allow", "deny"):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    for m in reversed(_VERDICT_RE.findall(text)):
        try:
            data = json.loads(m)
            if isinstance(data, dict) and data.get("verdict") in ("allow", "deny"):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return {"verdict": "allow", "reason": "(no verdict found in response)"}


def _fetch_attachment(master_url: str, attachment_id: str, filename: str, worktree: str) -> None:
    url = f"{master_url}/api/attachments/{attachment_id}/download"
    dest = Path(worktree) / filename
    with urllib.request.urlopen(url) as resp:
        dest.write_bytes(resp.read())
    LOGGER.info("agent.attachment_fetched id=%s filename=%s", attachment_id, filename)


def _stream_claude_once(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    session_id: str | None,
    is_new_session: bool,
    subagent: str | None,
    model: str | None,
    system_prompt: str | None,
    seq_start: int = 0,
    orch_env: dict | None = None,
) -> tuple[str, str | None, str | None, bool]:
    """Stream Claude stdout line by line, publishing each event as an MQTT chunk.

    Returns (output, new_session_id, transcript, is_error).
    """
    cmd = ["claude", "--print", "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions",
           "--mcp-config", _MCP_CONFIG]
    if session_id:
        if is_new_session:
            cmd += ["--session-id", session_id]
        else:
            cmd += ["--resume", session_id]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if subagent:
        cmd += ["--agent", subagent]
    chunk_topic = _chunk_topic(workspace_id, topic_id)
    events: list[dict] = []
    new_session_id: str | None = None
    output: str | None = None
    is_error = False
    seq = seq_start
    outputs: list[str] = []
    proc = None
    try:
        proc_env = {**os.environ, "TOPIC_ID": topic_id, **(orch_env or {})}
        proc = subprocess.Popen(
            cmd, cwd=worktree, env=proc_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            start_new_session=True,
        )
        proc.stdin.write(text)
        proc.stdin.close()
        with _active_procs_lock:
            _active_procs[reply_message_id] = proc
            _active_contexts[reply_message_id] = {
                "workspace_id": workspace_id,
                "topic_id": topic_id,
                "agent_name": agent_name,
            }
            _persist_active_procs_locked()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                LOGGER.warning("agent.llm_chunk_parse_error topic_id=%s line=%r", topic_id, line[:200])
                warn_event = {"type": "system", "subtype": "parse_warning", "line": line[:200]}
                client.publish(chunk_topic, json.dumps({
                    "message_id": reply_message_id,
                    "agent_name": agent_name,
                    "seq": seq,
                    "event": warn_event,
                }), qos=0)
                seq += 1
                continue
            events.append(event)
            client.publish(chunk_topic, json.dumps({
                "message_id": reply_message_id,
                "agent_name": agent_name,
                "seq": seq,
                "event": event,
            }), qos=0)
            LOGGER.debug("agent.llm_chunk topic_id=%s seq=%d type=%s", topic_id, seq, event.get("type"))
            seq += 1
            if event.get("type") == "result":
                new_session_id = event.get("session_id")
                result_text = event.get("result") or event.get("last_response")
                if result_text:
                    outputs.append(result_text)
                if event.get("is_error"):
                    is_error = True
        proc.wait()
        rc = proc.returncode
        output = "\n\n---\n\n".join(outputs) if outputs else None
        stderr_tail = ""
        if not output or rc != 0:
            stderr_tail = (proc.stderr.read() or "").strip()
            if not output:
                output = stderr_tail or "(no output)"
        transcript = json.dumps(events) if events else None
        if rc != 0:
            # Non-zero exit without an exception — claude exited cleanly with
            # a failure code. Surface enough postmortem to correlate with a
            # later alive=False ping (master keeps the proc reference past
            # this point, so the ping window is short but it exists).
            LOGGER.warning(
                "agent.llm_subprocess_nonzero_exit topic_id=%s message_id=%s pid=%s returncode=%d chunks=%d chars=%d stderr_tail=%r",
                topic_id, reply_message_id, proc.pid, rc,
                seq - seq_start, len(output), stderr_tail[-500:],
            )
            is_error = True
        LOGGER.info(
            "agent.llm_done topic_id=%s message_id=%s pid=%s returncode=%s chunks=%d chars=%d",
            topic_id, reply_message_id, proc.pid, rc,
            seq - seq_start, len(output),
        )
        return output, new_session_id, transcript, is_error
    except FileNotFoundError:
        LOGGER.error(
            "agent.llm_cli_missing topic_id=%s message_id=%s cmd=%s",
            topic_id, reply_message_id, cmd[0] if cmd else "claude",
        )
        return "(claude CLI not found in agent container)", None, None, True
    except Exception as exc:
        # Silent subprocess crashes (e.g. broken pipe on stdout, MQTT publish
        # raising mid-stream, an OS error while reading proc.stdout) were the
        # observed cause of "alive=False without agent.llm_done" in prod.
        # Capture proc state BEFORE killing the tree so the postmortem is
        # accurate, even if proc.poll() may already be None for a live proc
        # that we're about to terminate.
        rc = proc.poll() if proc is not None else None
        stderr_tail = ""
        if proc is not None and proc.stderr is not None:
            try:
                stderr_tail = (proc.stderr.read() or "").strip()
            except Exception:
                pass
        LOGGER.exception(
            "agent.llm_subprocess_aborted topic_id=%s message_id=%s pid=%s returncode=%s chunks=%d exc_type=%s exc=%s stderr_tail=%r",
            topic_id, reply_message_id,
            proc.pid if proc is not None else None,
            rc, seq - seq_start,
            type(exc).__name__, exc, stderr_tail[-500:],
        )
        if proc is not None:
            _kill_proc_tree(proc)
        with _active_procs_lock:
            _active_procs.pop(reply_message_id, None)
            _active_contexts.pop(reply_message_id, None)
            _persist_active_procs_locked()
        return f"(claude error: {exc})", None, None, True


def _run_claude(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    session_id: str | None,
    is_new_session: bool,
    subagent: str | None,
    model: str | None,
    system_prompt: str | None,
) -> tuple[str, str | None, str | None]:
    orch_env = getattr(_prompt_orch_env, "env", {})
    output, new_session_id, transcript, is_error = _stream_claude_once(
        client, workspace_id, topic_id, reply_message_id, agent_name,
        worktree, text, session_id, is_new_session, subagent, model, system_prompt,
        seq_start=0, orch_env=orch_env,
    )
    if not is_new_session and session_id and is_error and _SESSION_NOT_FOUND in (output or ""):
        LOGGER.warning("agent.session_expired sid=%s retrying_as_new", session_id)
        first_event_count = len(json.loads(transcript)) if transcript else 0
        seq = first_event_count
        client.publish(_chunk_topic(workspace_id, topic_id), json.dumps({
            "message_id": reply_message_id,
            "agent_name": agent_name,
            "seq": seq,
            "event": {"type": "system", "subtype": "retry"},
        }), qos=0)
        seq += 1
        output, new_session_id, transcript, _ = _stream_claude_once(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            worktree, text, session_id, True, subagent, model, system_prompt,
            seq_start=seq, orch_env=orch_env,
        )
    return output, new_session_id, transcript


def _stream_codex_once(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    model: str | None,
    session_id: str | None = None,
    is_new_session: bool = False,
    session_scope: str = "topic",
    seq_start: int = 0,
    orch_env: dict | None = None,
) -> tuple[str, str | None, str | None, bool]:
    """Stream Codex stdout line by line, publishing each event as an MQTT chunk.

    Returns (output, new_session_id, transcript, is_error).
    """
    fd, output_file = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    use_ephemeral = session_scope == "none"
    cmd = [
        "codex", "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-s", "danger-full-access",
    ]
    if use_ephemeral:
        cmd.append("--ephemeral")
    if model:
        cmd += ["-m", model]
    cmd += ["-o", output_file]
    if not use_ephemeral and session_id and not is_new_session:
        cmd += ["resume", session_id, "-"]
    chunk_topic = _chunk_topic(workspace_id, topic_id)
    events: list[dict] = []
    is_error = False
    seq = seq_start
    fallback_outputs: list[str] = []
    new_session_id: str | None = None
    proc = None
    try:
        proc_env = {**os.environ, "TOPIC_ID": topic_id, **(orch_env or {})}
        proc = subprocess.Popen(
            cmd, cwd=worktree, env=proc_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            start_new_session=True,
        )
        proc.stdin.write(text)
        proc.stdin.close()
        with _active_procs_lock:
            _active_procs[reply_message_id] = proc
            _active_contexts[reply_message_id] = {
                "workspace_id": workspace_id,
                "topic_id": topic_id,
                "agent_name": agent_name,
            }
            _persist_active_procs_locked()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    event = {"type": "output", "content": line}
                events.append(event)
                client.publish(chunk_topic, json.dumps({
                    "message_id": reply_message_id,
                    "agent_name": agent_name,
                    "seq": seq,
                    "event": event,
                }), qos=0)
                LOGGER.debug("agent.codex_chunk topic_id=%s seq=%d type=%s", topic_id, seq, event.get("type"))
                seq += 1
                ev_type = event.get("type", "")
                if ev_type == "thread.started":
                    sid = event.get("thread_id") or event.get("session_id")
                    if sid and not use_ephemeral:
                        new_session_id = sid
                elif ev_type == "turn.completed":
                    final = event.get("output_text") or event.get("last_message")
                    if final:
                        fallback_outputs.append(str(final))
                elif ev_type == "turn.failed":
                    is_error = True
                    err_msg = (event.get("error") or {}).get("message", "")
                    if err_msg:
                        fallback_outputs.append(f"(codex error: {err_msg})")
            proc.wait()
        finally:
            with _active_procs_lock:
                _active_procs.pop(reply_message_id, None)
                _active_contexts.pop(reply_message_id, None)
        rc = proc.returncode
        if rc and rc != 0 and not is_error:
            is_error = True
        output = None
        try:
            content = Path(output_file).read_text(encoding="utf-8").strip()
            if content:
                output = content
        except Exception:
            pass
        finally:
            try:
                Path(output_file).unlink()
            except Exception:
                pass
        if not output:
            output = "\n\n---\n\n".join(fallback_outputs) if fallback_outputs else None
        stderr_tail = ""
        if not output or (rc is not None and rc != 0):
            stderr_tail = (proc.stderr.read() or "").strip()
            if not output:
                output = stderr_tail or "(no output)"
        transcript = json.dumps(events) if events else None
        if rc is not None and rc != 0:
            LOGGER.warning(
                "agent.codex_subprocess_nonzero_exit topic_id=%s message_id=%s pid=%s returncode=%d chunks=%d chars=%d stderr_tail=%r",
                topic_id, reply_message_id, proc.pid, rc,
                seq - seq_start, len(output), stderr_tail[-500:],
            )
        LOGGER.info(
            "agent.codex_done topic_id=%s message_id=%s pid=%s returncode=%s chunks=%d chars=%d",
            topic_id, reply_message_id, proc.pid, rc,
            seq - seq_start, len(output),
        )
        return output, new_session_id, transcript, is_error
    except FileNotFoundError:
        LOGGER.error(
            "agent.codex_cli_missing topic_id=%s message_id=%s cmd=%s",
            topic_id, reply_message_id, cmd[0] if cmd else "codex",
        )
        try:
            Path(output_file).unlink()
        except Exception:
            pass
        return "(codex CLI not found in agent container)", None, None, True
    except Exception as exc:
        # See _stream_claude_once for rationale — same alive=False-without-done
        # pattern. Capture postmortem before kill_proc_tree.
        rc = proc.poll() if proc is not None else None
        stderr_tail = ""
        if proc is not None and proc.stderr is not None:
            try:
                stderr_tail = (proc.stderr.read() or "").strip()
            except Exception:
                pass
        LOGGER.exception(
            "agent.codex_subprocess_aborted topic_id=%s message_id=%s pid=%s returncode=%s chunks=%d exc_type=%s exc=%s stderr_tail=%r",
            topic_id, reply_message_id,
            proc.pid if proc is not None else None,
            rc, seq - seq_start,
            type(exc).__name__, exc, stderr_tail[-500:],
        )
        if proc is not None:
            _kill_proc_tree(proc)
        with _active_procs_lock:
            _active_procs.pop(reply_message_id, None)
            _active_contexts.pop(reply_message_id, None)
            _persist_active_procs_locked()
        try:
            Path(output_file).unlink()
        except Exception:
            pass
        return f"(codex error: {exc})", None, None, True


def _run_codex(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    model: str | None,
    session_id: str | None = None,
    is_new_session: bool = False,
    session_scope: str = "topic",
) -> tuple[str, str | None, str | None]:
    orch_env = getattr(_prompt_orch_env, "env", {})
    output, new_session_id, transcript, is_error = _stream_codex_once(
        client, workspace_id, topic_id, reply_message_id, agent_name,
        worktree, text, model, session_id, is_new_session, session_scope,
        orch_env=orch_env,
    )
    if not is_new_session and session_id and is_error and _CODEX_SESSION_NOT_FOUND in (output or ""):
        LOGGER.warning("agent.codex_session_expired sid=%s retrying_as_new", session_id)
        first_event_count = len(json.loads(transcript)) if transcript else 0
        seq = first_event_count
        client.publish(_chunk_topic(workspace_id, topic_id), json.dumps({
            "message_id": reply_message_id,
            "agent_name": agent_name,
            "seq": seq,
            "event": {"type": "system", "subtype": "retry"},
        }), qos=0)
        seq += 1
        output, new_session_id, transcript, _ = _stream_codex_once(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            worktree, text, model, session_id, True, session_scope,
            seq_start=seq, orch_env=orch_env,
        )
    return output, new_session_id, transcript


def _process_prompt(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    payload: dict,  # type: ignore[type-arg]
    repo_dir: str,
) -> None:
    message_id = payload.get("message_id") or str(uuid.uuid4())
    reply_message_id = str(uuid.uuid4())
    agent_name = payload.get("agent_name", "claude")
    worktree = payload.get("worktree", "")
    branch = payload.get("branch", "")
    repo_ref = payload.get("repo_ref", "")
    base_sha = payload.get("base_sha", "")
    text = payload.get("text", "")
    session_id = payload.get("session_id")
    is_new_session = bool(payload.get("is_new_session", False))
    session_scope = payload.get("session_scope", "topic")
    adapter = payload.get("adapter", "claude-code")
    subagent = payload.get("subagent")
    model = payload.get("model")
    system_prompt = payload.get("system_prompt")
    attachments = payload.get("attachments", [])
    master_url = payload.get("master_url", "http://master:8080")
    response_mode = payload.get("response_mode")
    task_id = payload.get("task_id")
    task_depth = int(payload.get("task_depth") or 0)

    # Store orchestration env vars in the thread-local so _run_claude/_run_codex
    # can read them without an extra parameter (their public signatures are
    # mocked in tests with fixed positional args).  The dict is then passed
    # explicitly from _run_* to _stream_*_once.
    _prompt_orch_env.env = {
        "AGENT_NAME": agent_name,
        "PROMPT_MESSAGE_ID": message_id,
        "TASK_DEPTH": str(task_depth),
        # Master requires this token on every orchestrate endpoint call; the
        # MCP server reads it from env. Without it every tool call 422s.
        "DISPATCH_TOKEN": payload.get("dispatch_token") or "",
    }

    if not text:
        LOGGER.warning("agent.empty_prompt topic_id=%s", topic_id)
        return

    client.publish(_status_topic(workspace_id, topic_id), json.dumps({"state": "thinking"}), qos=0)
    LOGGER.info("agent.llm_start topic_id=%s adapter=%s worktree=%s", topic_id, adapter, worktree)

    try:
        if worktree and branch and repo_dir:
            _ensure_worktree(repo_dir, worktree, branch, repo_ref, base_sha)
    except Exception:
        LOGGER.exception("agent.worktree_create_failed worktree=%s branch=%s", worktree, branch)

    cwd = worktree if (worktree and Path(worktree).exists()) else repo_dir or "/"

    # Fetch attachments into the working directory
    for att in attachments:
        try:
            _fetch_attachment(master_url, att["id"], att["filename"], cwd)
        except Exception:
            LOGGER.exception("agent.attachment_fetch_failed id=%s", att.get("id"))

    # Prepend attachment note to prompt text
    if attachments:
        note_lines = "\n".join(
            f'[Attached file: {a["filename"]} — available in the current directory]'
            for a in attachments
        )
        text = f"{note_lines}\n{text}"

    if adapter == "codex":
        response_text, new_session_id, transcript = _run_codex(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            cwd, text, model, session_id, is_new_session, session_scope,
        )
    else:
        # Claude sessions are scoped to the CWD (project directory).
        # For workspace/global scope we use a stable shared directory so
        # --resume works across different topic worktrees.
        session_cwd = cwd
        if session_scope == "workspace":
            session_cwd = f"/workspace/sessions/{workspace_id}"
            Path(session_cwd).mkdir(parents=True, exist_ok=True)
        elif session_scope == "global":
            session_cwd = "/workspace/sessions/global"
            Path(session_cwd).mkdir(parents=True, exist_ok=True)
        response_text, new_session_id, transcript = _run_claude(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            session_cwd, text, session_id, is_new_session, subagent, model, system_prompt,
        )

    LOGGER.info("agent.llm_done topic_id=%s chars=%d", topic_id, len(response_text))

    try:
        client.publish(
            _response_topic(workspace_id, topic_id),
            json.dumps({
                "message_id": reply_message_id,
                "agent_name": agent_name,
                "reply_to": message_id,
                "last_response": response_text,
                "transcript": transcript,
                "session_id": new_session_id,
            }),
            qos=1,
        )

        if response_mode == "verdict":
            verdict = _extract_verdict(response_text)
            client.publish(
                _verdict_topic(workspace_id, topic_id),
                json.dumps({
                    "reply_to": message_id,
                    "agent_name": agent_name,
                    "verdict": verdict["verdict"],
                    "reason": verdict.get("reason", ""),
                }),
                qos=1,
            )
            LOGGER.info(
                "agent.verdict_published topic_id=%s verdict=%s",
                topic_id,
                verdict["verdict"],
            )
    finally:
        # Keep proc in _active_procs until the response is queued so that
        # the stale-stream ping returns alive=True during the post-exit window.
        with _active_procs_lock:
            _active_procs.pop(reply_message_id, None)
            _active_contexts.pop(reply_message_id, None)
            _persist_active_procs_locked()

    client.publish(_status_topic(workspace_id, topic_id), json.dumps({"state": "idle"}), qos=0)


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    if reason_code.is_failure:
        LOGGER.error("agent.mqtt_connect_failed reason=%s", reason_code)
        return
    workspace_id = userdata["workspace_id"]
    prompt_sub = f"codex-slack/workspace/{workspace_id}/topic/+/prompt"
    cancel_sub = f"codex-slack/workspace/{workspace_id}/topic/+/cancel"
    ping_sub = f"codex-slack/workspace/{workspace_id}/topic/+/ping"
    client.subscribe(prompt_sub, qos=1)
    client.subscribe(cancel_sub, qos=1)
    client.subscribe(ping_sub, qos=0)
    LOGGER.info("agent.mqtt_connected subscribed=%s,%s,%s", prompt_sub, cancel_sub, ping_sub)
    # Emit `(message interrupted)` for any subprocess that didn't survive the
    # previous container life. No-op on the first start after a clean shutdown
    # (the file was unlinked by `_publish_interrupted_all`).
    _publish_inherited_interrupts(client)


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    LOGGER.warning("agent.mqtt_disconnected reason=%s", reason_code)


def _persist_active_procs_locked() -> None:
    """Atomically write `_active_contexts` to disk. Caller MUST hold
    `_active_procs_lock` so the snapshot matches the in-memory state.

    Failure to persist is non-fatal (logged) — the worst case is a missed
    `agent-killed` interrupt event after a SIGKILL, which is no worse than
    today's behavior."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = json.dumps(_active_contexts)
        tmp = _ACTIVE_PROCS_PATH.with_suffix(".json.tmp")
        tmp.write_text(snapshot)
        os.replace(tmp, _ACTIVE_PROCS_PATH)
    except Exception:
        LOGGER.exception("agent.persist_active_procs_failed")


def _publish_inherited_interrupts(client: mqtt.Client) -> None:
    """Emit `(message interrupted)` with reason `agent-killed` for any
    in-flight subprocesses that didn't survive the previous container life.

    The typical trigger is the container being SIGKILL'd while a subprocess
    was running — Python is just gone, so the SIGTERM handler never ran and
    master can only see `alive=False` on its next ping. Without this replay,
    master labels such messages `ping-timeout`, which is misleading.

    Fires at most once per agent process (guarded by `_inherited_already_published`)
    even if MQTT reconnects multiple times."""
    global _inherited_already_published
    if _inherited_already_published:
        return
    _inherited_already_published = True
    try:
        if not _ACTIVE_PROCS_PATH.exists():
            return
        try:
            data = json.loads(_ACTIVE_PROCS_PATH.read_text())
        except Exception:
            LOGGER.exception("agent.startup_inherited_parse_failed path=%s", _ACTIVE_PROCS_PATH)
            _ACTIVE_PROCS_PATH.unlink(missing_ok=True)
            return
        if not isinstance(data, dict) or not data:
            _ACTIVE_PROCS_PATH.unlink(missing_ok=True)
            return
        LOGGER.warning(
            "agent.startup_inherited_active count=%d message_ids=%s",
            len(data), list(data.keys()),
        )
        for message_id, ctx in data.items():
            if not isinstance(ctx, dict) or "workspace_id" not in ctx or "topic_id" not in ctx:
                LOGGER.warning("agent.startup_inherited_skip_bad_entry message_id=%s ctx=%r", message_id, ctx)
                continue
            try:
                client.publish(
                    _response_topic(ctx["workspace_id"], ctx["topic_id"]),
                    json.dumps({
                        "message_id": message_id,
                        "agent_name": ctx.get("agent_name"),
                        "reply_to": None,
                        "last_response": "(message interrupted)",
                        "interrupt_reason": "agent-killed",
                        "transcript": None,
                        "session_id": None,
                    }),
                    qos=1,
                )
                LOGGER.info("agent.startup_interrupt_published message_id=%s", message_id)
            except Exception:
                LOGGER.exception("agent.startup_interrupt_publish_failed message_id=%s", message_id)
        _ACTIVE_PROCS_PATH.unlink(missing_ok=True)
    except Exception:
        LOGGER.exception("agent.startup_inherited_unexpected")


def _publish_interrupted_all(client: mqtt.Client) -> None:
    """Kill active subprocesses and publish an interrupted response for each."""
    with _active_procs_lock:
        contexts = dict(_active_contexts)
        procs = dict(_active_procs)
    for message_id, ctx in contexts.items():
        proc = procs.get(message_id)
        if proc is not None:
            _kill_proc_tree(proc)
        try:
            client.publish(
                _response_topic(ctx["workspace_id"], ctx["topic_id"]),
                json.dumps({
                    "message_id": message_id,
                    "agent_name": ctx["agent_name"],
                    "reply_to": None,
                    "last_response": "(message interrupted)",
                    "interrupt_reason": "agent-shutdown",
                    "transcript": None,
                    "session_id": None,
                }),
                qos=1,
            )
            LOGGER.info("agent.sigterm_abort message_id=%s", message_id)
        except Exception:
            LOGGER.exception("agent.sigterm_abort_failed message_id=%s", message_id)
    # Clear the persisted snapshot so the restart doesn't double-publish with
    # interrupt_reason=agent-killed for the same messages we just handled.
    try:
        _ACTIVE_PROCS_PATH.unlink(missing_ok=True)
    except Exception:
        LOGGER.exception("agent.persist_active_procs_clear_failed")


def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    LOGGER.info("agent.mqtt_message topic=%s bytes=%d", msg.topic, len(msg.payload))

    parsed_ping = _parse_ping_topic(msg.topic)
    if parsed_ping is not None:
        workspace_id_ping, topic_id_ping = parsed_ping
        try:
            payload = json.loads(msg.payload)
            message_id = payload.get("message_id")
        except Exception:
            message_id = None
        if message_id:
            with _active_procs_lock:
                proc = _active_procs.get(message_id)
                active_count = len(_active_procs)
            alive = proc is not None
            client.publish(
                _pong_topic(workspace_id_ping, topic_id_ping),
                json.dumps({"message_id": message_id, "alive": alive}),
                qos=0,
            )
            if alive:
                LOGGER.info(
                    "agent.pong message_id=%s alive=True pid=%s",
                    message_id, proc.pid,
                )
            else:
                # alive=False means the proc is no longer in _active_procs.
                # The subprocess either exited normally (look up the prior
                # agent.llm_done / agent.codex_done line for returncode) or
                # aborted (look up the prior agent.llm_subprocess_aborted /
                # agent.codex_subprocess_aborted line for the exception and
                # stderr tail). active_procs_size aids correlation when many
                # streams are in flight.
                LOGGER.info(
                    "agent.pong message_id=%s alive=False active_procs_size=%d",
                    message_id, active_count,
                )
        return

    parsed_cancel = _parse_cancel_topic(msg.topic)
    if parsed_cancel is not None:
        _, topic_id = parsed_cancel
        try:
            payload = json.loads(msg.payload)
            cancel_msg_id = payload.get("message_id")
        except Exception:
            cancel_msg_id = None
        if cancel_msg_id:
            with _active_procs_lock:
                proc = _active_procs.get(cancel_msg_id)
            if proc is not None:
                LOGGER.info("agent.cancel message_id=%s pid=%s", cancel_msg_id, proc.pid)
                _kill_proc_tree(proc)
            else:
                LOGGER.info("agent.cancel_noop message_id=%s reason=not_found", cancel_msg_id)
        return

    parsed = _parse_prompt_topic(msg.topic)
    if parsed is None:
        return
    workspace_id, topic_id = parsed
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, ValueError):
        LOGGER.warning("agent.mqtt_parse_error topic=%s", msg.topic)
        return

    # Deduplicate QoS-1 redeliveries (clean_session=False can replay already-processed prompts).
    prompt_id = payload.get("message_id")
    if prompt_id:
        if prompt_id in _seen_prompt_ids:
            LOGGER.info("agent.skip_duplicate_prompt message_id=%s", prompt_id)
            return
        _seen_prompt_ids.add(prompt_id)
        _seen_prompt_ids_queue.append(prompt_id)
        if len(_seen_prompt_ids_queue) > _MAX_SEEN_PROMPT_IDS:
            _seen_prompt_ids.discard(_seen_prompt_ids_queue.popleft())

    repo_dir = userdata.get("repo_dir", "")
    master_url = userdata.get("master_url", "http://master:8080")
    # inject master_url into payload so _process_prompt can use it
    payload.setdefault("master_url", master_url)
    _executor.submit(_process_prompt, client, workspace_id, topic_id, payload, repo_dir)


def run_mqtt_loop(
    workspace_id: str,
    mqtt_host: str,
    mqtt_port: int,
    repo_dir: str = "",
    master_url: str = "http://master:8080",
) -> None:
    userdata = {"workspace_id": workspace_id, "repo_dir": repo_dir, "master_url": master_url}
    # Fixed client_id + clean_session=False: Mosquitto queues QoS-1 messages while
    # the agent is stopped and delivers them on reconnect, no matter how long boot takes.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"agent-{workspace_id}",
        clean_session=False,
        userdata=userdata,
    )
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect(mqtt_host, mqtt_port, keepalive=60)
    LOGGER.info("agent.mqtt_loop_start workspace_id=%s host=%s port=%s", workspace_id, mqtt_host, mqtt_port)

    global _mqtt_client_ref
    _mqtt_client_ref = client

    def _sigterm_handler(signum, frame):
        LOGGER.info("agent.sigterm_received active=%d", len(_active_procs))
        _publish_interrupted_all(client)
        import time
        time.sleep(1)  # allow MQTT to flush QoS-1 messages
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    client.loop_forever()
