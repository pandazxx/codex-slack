from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .service import CommandResult, MasterService


@dataclass(frozen=True)
class ProvisionRequest:
    name: str
    platform: str
    repo_path: str | None
    channel_id: str | None
    repo_ref: str
    agent_adapter: str | None
    create_repo: bool
    repo_owner: str | None
    repo_name: str | None
    repo_visibility: str
    create_channel: bool
    channel_name: str | None
    channel_visibility: str


@dataclass(frozen=True)
class ProvisionContext:
    platform: str
    admin_channel_id: str
    discord_guild_id: str | None = None
    discord_category_id: str | None = None
    discord_event_loop: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class CreatedRepo:
    provider: str
    owner: str
    repo_name: str
    visibility: str
    html_url: str
    clone_url: str
    ssh_url: str


@dataclass(frozen=True)
class CreatedChannel:
    platform: str
    channel_id: str
    channel_name: str
    visibility: str | None = None
    guild_id: str | None = None
    category_id: str | None = None


class RepoProvisioner(Protocol):
    def create_repo(
        self,
        *,
        agent_name: str,
        owner: str | None,
        repo_name: str | None,
        visibility: str,
    ) -> CreatedRepo:
        ...


class ChannelProvisioner(Protocol):
    def create_channel(
        self,
        *,
        agent_name: str,
        channel_name: str | None,
        visibility: str,
        context: ProvisionContext,
    ) -> CreatedChannel:
        ...


def normalize_repo_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    return value or "agent-repo"


def normalize_channel_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "agent"


class GitHubRepoProvisioner:
    api_base_url = "https://api.github.com"

    def __init__(self, token: str | None = None) -> None:
        resolved = (token or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
        self._token = resolved

    def create_repo(
        self,
        *,
        agent_name: str,
        owner: str | None,
        repo_name: str | None,
        visibility: str,
    ) -> CreatedRepo:
        if not self._token:
            raise RuntimeError("GitHub repo provisioning requires GH_TOKEN or GITHUB_TOKEN")

        resolved_repo_name = normalize_repo_name(repo_name or agent_name)
        if visibility not in {"private", "public"}:
            raise RuntimeError(f"unsupported repo visibility: {visibility}")

        token_owner, token_owner_type = self._fetch_token_identity()
        resolved_owner = owner or token_owner
        is_org = token_owner_type.lower() == "organization" or (owner is not None and owner != token_owner)
        payload = {
            "name": resolved_repo_name,
            "private": visibility == "private",
        }
        if is_org:
            response = self._request("POST", f"/orgs/{resolved_owner}/repos", payload)
        else:
            response = self._request("POST", "/user/repos", payload)

        return CreatedRepo(
            provider="github",
            owner=str(response["owner"]["login"]),
            repo_name=str(response["name"]),
            visibility="private" if bool(response.get("private")) else "public",
            html_url=str(response["html_url"]),
            clone_url=str(response["clone_url"]),
            ssh_url=str(response["ssh_url"]),
        )

    def _fetch_token_identity(self) -> tuple[str, str]:
        payload = self._request("GET", "/user")
        return str(payload["login"]), str(payload.get("type") or "User")

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.api_base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "codex-slack-master",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"github api error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"github api request failed: {exc}") from exc

        return json.loads(raw) if raw else {}


class SlackChannelProvisioner:
    def __init__(self, client: object) -> None:
        self._client = client

    def create_channel(
        self,
        *,
        agent_name: str,
        channel_name: str | None,
        visibility: str,
        context: ProvisionContext,
    ) -> CreatedChannel:
        if visibility not in {"private", "public"}:
            raise RuntimeError(f"unsupported slack channel visibility: {visibility}")
        resolved_name = normalize_channel_name(channel_name or f"agent-{agent_name}")
        result = self._client.conversations_create(name=resolved_name, is_private=(visibility == "private"))
        channel = result.get("channel", {})
        channel_id = str(channel.get("id") or "")
        if not channel_id:
            raise RuntimeError("slack channel create returned no channel id")
        return CreatedChannel(
            platform="slack",
            channel_id=channel_id,
            channel_name=str(channel.get("name") or resolved_name),
            visibility=visibility,
        )


class DiscordChannelProvisioner:
    def __init__(self, client: object) -> None:
        self._client = client

    def create_channel(
        self,
        *,
        agent_name: str,
        channel_name: str | None,
        visibility: str,
        context: ProvisionContext,
    ) -> CreatedChannel:
        if context.discord_event_loop is None:
            raise RuntimeError("discord provisioning requires an event loop")
        if not context.discord_guild_id:
            raise RuntimeError("discord provisioning requires a guild id")
        resolved_name = normalize_channel_name(channel_name or f"agent-{agent_name}")

        async def _create() -> CreatedChannel:
            guild = self._client.get_guild(int(context.discord_guild_id))
            if guild is None:
                guild = await self._client.fetch_guild(int(context.discord_guild_id))
            category = None
            if context.discord_category_id:
                category = self._client.get_channel(int(context.discord_category_id))
                if category is None:
                    category = await self._client.fetch_channel(int(context.discord_category_id))
            created = await guild.create_text_channel(resolved_name, category=category)
            return CreatedChannel(
                platform="discord",
                channel_id=str(created.id),
                channel_name=str(created.name),
                visibility=visibility,
                guild_id=str(guild.id),
                category_id=str(getattr(category, "id", "") or ""),
            )

        future = asyncio.run_coroutine_threadsafe(_create(), context.discord_event_loop)
        return future.result(timeout=30)


class ProvisioningCoordinator:
    def __init__(
        self,
        *,
        service: MasterService,
        repo_provisioner: RepoProvisioner | None = None,
        channel_provisioner: ChannelProvisioner | None = None,
    ) -> None:
        self._service = service
        self._repo_provisioner = repo_provisioner
        self._channel_provisioner = channel_provisioner

    def provision_agent(self, request: ProvisionRequest, *, context: ProvisionContext) -> CommandResult:
        if not request.repo_path and not request.create_repo:
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="repo_path is required unless --create-repo is set",
                data={"field": "repo_path"},
            )
        if not request.channel_id and not request.create_channel:
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="channel_id is required unless --create-channel is set",
                data={"field": "channel_id"},
            )

        created_repo: CreatedRepo | None = None
        created_channel: CreatedChannel | None = None
        resolved_repo_path = request.repo_path
        resolved_channel_id = request.channel_id

        try:
            if request.create_repo:
                if self._repo_provisioner is None:
                    raise RuntimeError("repo provisioner is not configured")
                created_repo = self._repo_provisioner.create_repo(
                    agent_name=request.name,
                    owner=request.repo_owner,
                    repo_name=request.repo_name,
                    visibility=request.repo_visibility,
                )
                resolved_repo_path = created_repo.ssh_url or created_repo.clone_url

            if request.create_channel:
                if self._channel_provisioner is None:
                    raise RuntimeError("channel provisioner is not configured")
                created_channel = self._channel_provisioner.create_channel(
                    agent_name=request.name,
                    channel_name=request.channel_name,
                    visibility=request.channel_visibility,
                    context=context,
                )
                resolved_channel_id = created_channel.channel_id
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                ok=False,
                code="ERR_PROVISION_FAILED",
                message=str(exc),
                data={
                    "created_repo": created_repo.__dict__ if created_repo else None,
                    "created_channel": created_channel.__dict__ if created_channel else None,
                },
            )

        load_result = self._service.load_agent(
            name=request.name,
            repo_path=resolved_repo_path or "",
            channel_id=resolved_channel_id or "",
            repo_ref=request.repo_ref,
            platform=request.platform,
            agent_adapter=request.agent_adapter,
        )
        if not load_result.ok:
            data = dict(load_result.data)
            if created_repo:
                data["created_repo"] = created_repo.__dict__
            if created_channel:
                data["created_channel"] = created_channel.__dict__
            return CommandResult(
                ok=False,
                code=load_result.code,
                message=load_result.message,
                data=data,
            )

        data = dict(load_result.data)
        if created_repo:
            data["created_repo"] = created_repo.__dict__
        if created_channel:
            data["created_channel"] = created_channel.__dict__
        return CommandResult(
            ok=True,
            code="OK",
            message=f"provisioned {request.name}",
            data=data,
        )
