from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

from .registry import AgentRecord, AgentRegistry
from .runtime_adapter import RuntimeAdapter

DEFAULT_IMAGE = "codex-slack-bot:latest"
DEFAULT_RUNTIME = "podman"
DEFAULT_AGENT_ADAPTER = "codex"
SUPPORTED_AGENT_ADAPTERS = {"codex", "claude-code"}
GLOBAL_CODEX_CONFIG_MOUNT = "/run/secrets/master_codex_config"
GLOBAL_CLAUDE_CONFIG_MOUNT = "/run/secrets/master_claude_config"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    code: str
    message: str
    data: dict[str, object]


class MasterService:
    def __init__(
        self,
        registry: AgentRegistry,
        runtime: RuntimeAdapter,
        default_image: str = DEFAULT_IMAGE,
        agent_codex_auth_json_path: str | None = None,
        agent_codex_config_dir_path: str | None = None,
        agent_claude_config_dir_path: str | None = None,
        agent_ssh_auth_sock_path: str | None = None,
        agent_ssh_known_hosts_path: str | None = None,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        default_agent_adapter: str = DEFAULT_AGENT_ADAPTER,
    ) -> None:
        self._registry = registry
        self._runtime = runtime
        self._default_image = default_image
        self._agent_codex_auth_json_path = agent_codex_auth_json_path
        self._agent_codex_config_dir_path = agent_codex_config_dir_path
        self._agent_claude_config_dir_path = agent_claude_config_dir_path
        self._agent_ssh_auth_sock_path = agent_ssh_auth_sock_path
        self._agent_ssh_known_hosts_path = agent_ssh_known_hosts_path
        self._git_user_name = git_user_name
        self._git_user_email = git_user_email
        self._default_agent_adapter = default_agent_adapter if default_agent_adapter in SUPPORTED_AGENT_ADAPTERS else DEFAULT_AGENT_ADAPTER

    def list_agents(self) -> CommandResult:
        agents = []
        for record in self._registry.list_agents():
            d = record.to_dict()
            inspect = self._runtime.inspect_agent(record.container_name)
            if inspect:
                state = inspect.get("State", {})
                d["container_state"] = state.get("Status", "unknown")
                d["container_running"] = bool(state.get("Running", False))
            else:
                d["container_state"] = "absent"
                d["container_running"] = False
            agents.append(d)
        result = CommandResult(ok=True, code="OK", message="agents listed", data={"agents": agents})
        self._audit(command="list", agent="-", result=result)
        return result

    def load_agent(
        self,
        *,
        name: str,
        repo_path: str,
        channel_id: str,
        repo_ref: str = "main",
        platform: str = "slack",
        agent_adapter: str | None = None,
    ) -> CommandResult:
        validation_error = self._validate_load_inputs(name=name, channel_id=channel_id, platform=platform)
        if validation_error:
            self._audit(command="load", agent=name or "-", result=validation_error)
            return validation_error
        if agent_adapter and agent_adapter not in SUPPORTED_AGENT_ADAPTERS:
            result = CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message=f"unsupported agent adapter: {agent_adapter}",
                data={"field": "agent_adapter", "supported": sorted(SUPPORTED_AGENT_ADAPTERS)},
            )
            self._audit(command="load", agent=name or "-", result=result)
            return result

        try:
            self._clear_cached_checkout(name)
            repo_source = self._normalize_repo_source(repo_path)
            checkout_path, resolved_repo_ref = self._checkout_repo_source_with_fallback(
                name=name,
                repo_source=repo_source,
                repo_ref=repo_ref,
            )
        except Exception as exc:  # noqa: BLE001
            result = CommandResult(
                ok=False,
                code="ERR_REPO_NOT_ALLOWED",
                message=f"repo path is not accessible: {repo_path} ({exc})",
                data={},
            )
            self._audit(command="load", agent=name, result=result)
            return result

        conflict = self._registry.find_by_channel(channel_id, platform=platform)
        if conflict and conflict.name != name:
            result = CommandResult(
                ok=False,
                code="ERR_CHANNEL_CONFLICT",
                message=f"channel {platform}:{channel_id} already bound to {conflict.name}",
                data={"owner": conflict.name, "platform": platform},
            )
            self._audit(command="load", agent=name, result=result)
            return result

        image_plan = self._resolve_image_plan(str(checkout_path))
        record = self._registry.get(name)
        selected_adapter = agent_adapter or (record.agent_adapter if record else self._default_agent_adapter)
        if not record:
            record = AgentRecord(
                name=name,
                repo_path=str(checkout_path),
                channel_id=channel_id,
                container_name=f"agent-{name}",
                runtime=DEFAULT_RUNTIME,
                image_plan=image_plan,
                status="loaded",
                platform=platform,
                agent_adapter=selected_adapter,
                repo_source=repo_source,
                repo_ref=resolved_repo_ref,
            )
        else:
            record.repo_path = str(checkout_path)
            record.channel_id = channel_id
            record.platform = platform
            record.image_plan = image_plan
            record.status = "loaded"
            record.last_error = None
            record.repo_source = repo_source
            record.repo_ref = resolved_repo_ref
            record.agent_adapter = selected_adapter

        saved = self._registry.upsert(record)
        result = CommandResult(
            ok=True,
            code="OK",
            message=f"loaded {name}",
            data={
                "state": saved.status,
                "image_plan": saved.image_plan,
                "platform": saved.platform,
                "channel_id": saved.channel_id,
                "repo_ref": saved.repo_ref,
                "agent_adapter": saved.agent_adapter,
            },
        )
        self._audit(command="load", agent=name, result=result)
        return result

    def start_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="start", agent=name, result=result)
            return result

        try:
            image = self._default_image
            if record.image_plan.get("type") == "dockerfile":
                image = self._runtime.build_image(
                    name=record.name,
                    repo_path=record.repo_path,
                    context_rel=str(record.image_plan["context"]),
                    dockerfile_rel=str(record.image_plan["dockerfile"]),
                )

            self._runtime.create_or_update_agent(
                container_name=record.container_name,
                image=image,
                repo_volume=f"agent-workspace-{record.name}",
                env=self._build_agent_env(record),
                mounts=self._build_agent_mounts(),
            )
            self._runtime.start_agent(record.container_name)
        except Exception as exc:  # noqa: BLE001
            record.status = "error"
            record.last_error = str(exc)
            self._registry.upsert(record)
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to start {name}: {exc}",
                data={"state": record.status},
            )
            self._audit(command="start", agent=name, result=result)
            return result

        record.status = "running"
        record.resolved_image = image
        record.last_error = None
        self._registry.upsert(record)

        result = CommandResult(
            ok=True,
            code="OK",
            message=f"started {name}",
            data={"state": record.status, "container_name": record.container_name, "resolved_image": image},
        )
        self._audit(command="start", agent=name, result=result)
        return result

    def stop_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="stop", agent=name, result=result)
            return result

        try:
            self._runtime.stop_agent(record.container_name)
        except Exception as exc:  # noqa: BLE001
            record.status = "error"
            record.last_error = str(exc)
            self._registry.upsert(record)
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to stop {name}: {exc}",
                data={"state": record.status},
            )
            self._audit(command="stop", agent=name, result=result)
            return result

        record.status = "stopped"
        record.last_error = None
        self._registry.upsert(record)
        result = CommandResult(ok=True, code="OK", message=f"stopped {name}", data={"state": record.status})
        self._audit(command="stop", agent=name, result=result)
        return result

    def remove_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="remove", agent=name, result=result)
            return result

        try:
            self._runtime.remove_agent(record.container_name)
        except Exception as exc:  # noqa: BLE001
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to remove {name}: {exc}",
                data={},
            )
            self._audit(command="remove", agent=name, result=result)
            return result

        self._registry.remove(name)
        self._clear_cached_checkout(name)
        result = CommandResult(ok=True, code="OK", message=f"removed {name}", data={"removed": True})
        self._audit(command="remove", agent=name, result=result)
        return result

    def status(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="status", agent=name, result=result)
            return result

        inspect = self._runtime.inspect_agent(record.container_name)
        result = CommandResult(
            ok=True,
            code="OK",
            message=f"status {name}",
            data={"record": record.to_dict(), "runtime": inspect},
        )
        self._audit(command="status", agent=name, result=result)
        return result

    def refresh_agent_auth(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="refresh-auth", agent=name, result=result)
            return result

        if not self._agent_codex_auth_json_path:
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message="agent codex auth source is not configured",
                data={},
            )
            self._audit(command="refresh-auth", agent=name, result=result)
            return result

        try:
            self._runtime.refresh_agent_auth(
                volume_name=f"agent-workspace-{record.name}",
                host_auth_path=self._agent_codex_auth_json_path,
            )
        except Exception as exc:  # noqa: BLE001
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to refresh auth for {name}: {exc}",
                data={},
            )
            self._audit(command="refresh-auth", agent=name, result=result)
            return result

        result = CommandResult(
            ok=True,
            code="OK",
            message=f"refreshed auth for {name}",
            data={
                "refreshed": True,
                "volume_name": f"agent-workspace-{record.name}",
                "auth_source": self._agent_codex_auth_json_path,
            },
        )
        self._audit(command="refresh-auth", agent=name, result=result)
        return result

    def refresh_agent_config(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="refresh-config", agent=name, result=result)
            return result

        if not self._agent_claude_config_dir_path:
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message="agent claude config source is not configured",
                data={},
            )
            self._audit(command="refresh-config", agent=name, result=result)
            return result

        try:
            self._runtime.refresh_agent_config(
                volume_name=f"agent-workspace-{record.name}",
                host_config_dir=self._agent_claude_config_dir_path,
            )
        except Exception as exc:  # noqa: BLE001
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to refresh config for {name}: {exc}",
                data={},
            )
            self._audit(command="refresh-config", agent=name, result=result)
            return result

        result = CommandResult(
            ok=True,
            code="OK",
            message=f"refreshed config for {name}",
            data={
                "refreshed": True,
                "volume_name": f"agent-workspace-{record.name}",
                "config_source": self._agent_claude_config_dir_path,
            },
        )
        self._audit(command="refresh-config", agent=name, result=result)
        return result

    def set_agent_model(self, *, name: str, model: str | None) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="set-model", agent=name, result=result)
            return result

        record.claude_model = model
        self._registry.upsert(record)
        result = CommandResult(
            ok=True,
            code="OK",
            message=f"model set to {model!r} for {name}" if model else f"model cleared for {name}",
            data={"name": name, "claude_model": model},
        )
        self._audit(command="set-model", agent=name, result=result)
        return result

    def _validate_load_inputs(self, *, name: str, channel_id: str, platform: str) -> CommandResult | None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", name):
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid agent name",
                data={"field": "name"},
            )
        if platform not in {"slack", "discord"}:
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid platform",
                data={"field": "platform"},
            )
        if platform == "slack" and not channel_id.startswith(("C", "G")):
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid channel id",
                data={"field": "channel_id"},
            )
        if platform == "discord" and not re.fullmatch(r"[0-9]{6,}", channel_id):
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid discord channel id",
                data={"field": "channel_id"},
            )
        return None

    @staticmethod
    def _audit(*, command: str, agent: str, result: CommandResult) -> None:
        LOGGER.info(
            "master.audit command=%s agent=%s ok=%s code=%s message=%s",
            command,
            agent,
            result.ok,
            result.code,
            result.message,
        )

    def _build_agent_env(self, record: AgentRecord) -> dict[str, str]:
        env = {
            "CODEX_CONTAINER_MODE": "agent-worker",
            "HOME": "/workspace/home",
            "XDG_CONFIG_HOME": "/workspace/home/.config",
            "CODEX_HOME": "/workspace/.codex",
            "AGENT_REPO_URL": record.repo_source or record.repo_path,
            "AGENT_REPO_REF": record.repo_ref,
            "AGENT_REPO_DIR": "repo",
        }

        gh_token = os.getenv("GH_TOKEN", "").strip()
        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        claude_code_oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

        if gh_token:
            env["GH_TOKEN"] = gh_token
        if github_token:
            env["GITHUB_TOKEN"] = github_token
        if openai_api_key:
            env["OPENAI_API_KEY"] = openai_api_key
        if claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = claude_code_oauth_token
        elif anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = anthropic_api_key
        if self._agent_codex_config_dir_path:
            env["AGENT_GLOBAL_CODEX_CONFIG_DIR"] = GLOBAL_CODEX_CONFIG_MOUNT
        if self._agent_claude_config_dir_path:
            env["AGENT_GLOBAL_CLAUDE_CONFIG_DIR"] = GLOBAL_CLAUDE_CONFIG_MOUNT
        if self._git_user_name:
            env["AGENT_GIT_USER_NAME"] = self._git_user_name
        if self._git_user_email:
            env["AGENT_GIT_USER_EMAIL"] = self._git_user_email
        if self._agent_ssh_auth_sock_path:
            env["SSH_AUTH_SOCK"] = "/run/secrets/ssh-auth.sock"
            env["GIT_SSH_COMMAND"] = self._agent_git_ssh_command()
        # CLAUDE_CONFIG_DIR is intentionally NOT set here. The entrypoint copies
        # /run/secrets/master_claude_config into ~/.claude/ so claude can write
        # session data there. Pointing CLAUDE_CONFIG_DIR at the read-only mount
        # causes claude to hang on its first write attempt.

        return env

    def _build_agent_mounts(self) -> list[str]:
        mounts: list[str] = []
        if self._agent_codex_auth_json_path:
            mounts.append(f"{self._agent_codex_auth_json_path}:/run/secrets/codex_auth.json:ro")
        if self._agent_codex_config_dir_path:
            mounts.append(f"{self._agent_codex_config_dir_path}:{GLOBAL_CODEX_CONFIG_MOUNT}:ro")
        if self._agent_claude_config_dir_path:
            mounts.append(f"{self._agent_claude_config_dir_path}:{GLOBAL_CLAUDE_CONFIG_MOUNT}:ro")
        if self._agent_ssh_auth_sock_path:
            mounts.append(f"{self._agent_ssh_auth_sock_path}:/run/secrets/ssh-auth.sock")
        if self._agent_ssh_known_hosts_path:
            mounts.append(f"{self._agent_ssh_known_hosts_path}:/run/secrets/ssh_known_hosts:ro")
        return mounts

    def _agent_git_ssh_command(self) -> str:
        if self._agent_ssh_known_hosts_path:
            return "ssh -o UserKnownHostsFile=/run/secrets/ssh_known_hosts"
        return "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    def _master_git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._agent_ssh_auth_sock_path:
            env["SSH_AUTH_SOCK"] = "/ssh-agent"
            LOGGER.info("master.git_env ssh_auth_sock=/ssh-agent (from %s)", self._agent_ssh_auth_sock_path)
        else:
            LOGGER.info("master.git_env ssh_auth_sock=none (MASTER_AGENT_SSH_AUTH_SOCK_PATH not set)")
        if self._agent_ssh_known_hosts_path:
            env["GIT_SSH_COMMAND"] = f"ssh -o UserKnownHostsFile={self._agent_ssh_known_hosts_path}"
            LOGGER.info("master.git_env GIT_SSH_COMMAND=ssh -o UserKnownHostsFile=%s", self._agent_ssh_known_hosts_path)
        else:
            env["GIT_SSH_COMMAND"] = "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            LOGGER.info("master.git_env GIT_SSH_COMMAND=ssh -o StrictHostKeyChecking=no (no known_hosts configured)")
        return env

    def _resolve_image_plan(self, repo_path: str) -> dict[str, str]:
        dockerfile = Path(repo_path) / ".prj_assistant" / "image" / "Dockerfile"
        if dockerfile.exists():
            return {
                "type": "dockerfile",
                "dockerfile": ".prj_assistant/image/Dockerfile",
                "context": ".prj_assistant/image",
            }
        return {"type": "default", "image": self._default_image}

    @staticmethod
    def _normalize_repo_source(repo_input: str) -> str:
        candidate = repo_input.strip()
        if not candidate:
            raise ValueError("empty repo source")

        local_path = Path(candidate)
        if local_path.exists():
            resolved = str(local_path.resolve())
            LOGGER.info("master.repo_source input=%r resolved=local path=%s", repo_input, resolved)
            return resolved

        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
            url = f"https://github.com/{candidate}.git"
            LOGGER.info("master.repo_source input=%r resolved=github url=%s", repo_input, url)
            return url

        if candidate.startswith(("https://", "http://", "ssh://", "git@", "file://")):
            LOGGER.info("master.repo_source input=%r resolved=explicit url=%s", repo_input, candidate)
            return candidate

        raise ValueError("unsupported repo source")

    def _cached_checkout_path(self, name: str) -> Path:
        return self._registry.path.parent / "repo-cache" / name

    def _clear_cached_checkout(self, name: str) -> None:
        checkout_path = self._cached_checkout_path(name)
        if checkout_path.exists():
            shutil.rmtree(checkout_path)

    def _checkout_repo_source(self, *, name: str, repo_source: str, repo_ref: str) -> Path:
        source_path = Path(repo_source)
        if source_path.exists():
            LOGGER.info("master.checkout agent=%s source=local path=%s ref=%s", name, repo_source, repo_ref)
            return source_path

        checkout_root = self._registry.path.parent / "repo-cache"
        checkout_root.mkdir(parents=True, exist_ok=True)
        checkout_path = checkout_root / name

        LOGGER.info("master.checkout agent=%s source=remote url=%s ref=%s dest=%s", name, repo_source, repo_ref, checkout_path)
        self._run_git(["git", "clone", "--branch", repo_ref, repo_source, str(checkout_path)])
        LOGGER.info("master.checkout_done agent=%s dest=%s", name, checkout_path)
        return checkout_path

    def _checkout_repo_source_with_fallback(self, *, name: str, repo_source: str, repo_ref: str) -> tuple[Path, str]:
        refs_to_try = [repo_ref]
        if repo_ref == "main":
            refs_to_try.append("master")

        last_error: Exception | None = None
        for candidate_ref in refs_to_try:
            try:
                return self._checkout_repo_source(name=name, repo_source=repo_source, repo_ref=candidate_ref), candidate_ref
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        if last_error is None:
            raise RuntimeError("no repo ref candidates available")
        raise last_error

    def _run_git(self, args: list[str], cwd: Path | None = None) -> None:
        env = self._master_git_env()
        LOGGER.info("master.run_git cmd=%r cwd=%s GIT_SSH_COMMAND=%r", " ".join(args), cwd or ".", env.get("GIT_SSH_COMMAND", "-"))
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            LOGGER.error("master.run_git_failed cmd=%r returncode=%d stderr=%s", " ".join(args), completed.returncode, completed.stderr.strip())
            raise RuntimeError(completed.stderr.strip() or f"git command failed: {' '.join(args)}")
        LOGGER.info("master.run_git_ok cmd=%r", " ".join(args))
