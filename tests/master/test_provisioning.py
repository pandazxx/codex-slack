from __future__ import annotations

from src.master.command_dispatch import parse_provision_text
from src.master.provisioning import (
    CreatedChannel,
    CreatedRepo,
    ProvisionContext,
    ProvisionRequest,
    ProvisioningCoordinator,
    SlackChannelProvisioner,
)
from src.master.service import CommandResult


class FakeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "name": name,
                "repo_path": repo_path,
                "channel_id": channel_id,
                "repo_ref": repo_ref,
                "platform": platform,
                "agent_adapter": agent_adapter,
            }
        )
        return CommandResult(
            ok=True,
            code="OK",
            message="loaded",
            data={"name": name, "repo_path": repo_path, "channel_id": channel_id, "platform": platform},
        )


class FakeRepoProvisioner:
    def create_repo(
        self,
        *,
        agent_name: str,
        owner: str | None,
        repo_name: str | None,
        visibility: str,
    ) -> CreatedRepo:
        return CreatedRepo(
            provider="github",
            owner=owner or "token-owner",
            repo_name=repo_name or agent_name,
            visibility=visibility,
            default_branch="main",
            html_url=f"https://github.com/{owner or 'token-owner'}/{repo_name or agent_name}",
            clone_url=f"https://github.com/{owner or 'token-owner'}/{repo_name or agent_name}.git",
            ssh_url=f"git@github.com:{owner or 'token-owner'}/{repo_name or agent_name}.git",
        )


class FakeChannelProvisioner:
    def create_channel(
        self,
        *,
        agent_name: str,
        channel_name: str | None,
        visibility: str,
        context: ProvisionContext,
    ) -> CreatedChannel:
        return CreatedChannel(
            platform=context.platform,
            channel_id="C999" if context.platform == "slack" else "123456789012345678",
            channel_name=channel_name or f"agent-{agent_name}",
            visibility=visibility,
            guild_id=context.discord_guild_id,
            category_id=context.discord_category_id,
        )


def test_parse_provision_text_accepts_existing_resources() -> None:
    parsed = parse_provision_text("payments /tmp/repo C123")
    assert parsed == ProvisionRequest(
        name="payments",
        platform="slack",
        repo_path="/tmp/repo",
        channel_id="C123",
        repo_ref="main",
        agent_adapter=None,
        create_repo=False,
        repo_owner=None,
        repo_name=None,
        repo_visibility="private",
        create_channel=False,
        channel_name=None,
        channel_visibility="private",
    )


def test_parse_provision_text_defaults_to_creating_missing_repo_and_channel() -> None:
    parsed = parse_provision_text("payments")
    assert parsed.repo_path is None
    assert parsed.channel_id is None
    assert parsed.create_repo is True
    assert parsed.create_channel is True


def test_parse_provision_text_defaults_to_creating_only_missing_resource() -> None:
    parsed = parse_provision_text("payments /tmp/repo")
    assert parsed.repo_path == "/tmp/repo"
    assert parsed.channel_id is None
    assert parsed.create_repo is False
    assert parsed.create_channel is True


def test_parse_provision_text_accepts_create_flags() -> None:
    parsed = parse_provision_text(
        'payments --branch main --create-repo --repo-owner pandazxx --repo-name pay-api --repo-visibility public '
        '--create-channel --channel-name "agent-payments" --channel-visibility public --adapter claude-code'
    )
    assert parsed.name == "payments"
    assert parsed.repo_path is None
    assert parsed.repo_ref == "main"
    assert parsed.create_repo is True
    assert parsed.repo_owner == "pandazxx"
    assert parsed.repo_name == "pay-api"
    assert parsed.repo_visibility == "public"
    assert parsed.create_channel is True
    assert parsed.channel_name == "agent-payments"
    assert parsed.channel_visibility == "public"
    assert parsed.agent_adapter == "claude-code"


def test_provisioning_coordinator_creates_repo_and_channel_before_load() -> None:
    service = FakeService()
    coordinator = ProvisioningCoordinator(
        service=service,  # type: ignore[arg-type]
        repo_provisioner=FakeRepoProvisioner(),
        channel_provisioner=FakeChannelProvisioner(),
    )

    result = coordinator.provision_agent(
        ProvisionRequest(
            name="payments",
            platform="slack",
            repo_path=None,
            channel_id=None,
            repo_ref="main",
            agent_adapter="codex",
            create_repo=True,
            repo_owner=None,
            repo_name=None,
            repo_visibility="private",
            create_channel=True,
            channel_name=None,
            channel_visibility="private",
        ),
        context=ProvisionContext(platform="slack", admin_channel_id="CADMIN"),
    )

    assert result.ok is True
    assert service.calls == [
        {
            "name": "payments",
            "repo_path": "git@github.com:token-owner/payments.git",
            "channel_id": "C999",
            "repo_ref": "main",
            "platform": "slack",
            "agent_adapter": "codex",
        }
    ]
    assert result.data["created_repo"]["owner"] == "token-owner"
    assert result.data["created_repo"]["ssh_url"] == "git@github.com:token-owner/payments.git"
    assert result.data["created_channel"]["channel_id"] == "C999"


def test_provisioning_coordinator_uses_created_repo_default_branch() -> None:
    service = FakeService()

    class TrunkRepoProvisioner(FakeRepoProvisioner):
        def create_repo(
            self,
            *,
            agent_name: str,
            owner: str | None,
            repo_name: str | None,
            visibility: str,
        ) -> CreatedRepo:
            created = super().create_repo(
                agent_name=agent_name,
                owner=owner,
                repo_name=repo_name,
                visibility=visibility,
            )
            return CreatedRepo(
                provider=created.provider,
                owner=created.owner,
                repo_name=created.repo_name,
                visibility=created.visibility,
                default_branch="trunk",
                html_url=created.html_url,
                clone_url=created.clone_url,
                ssh_url=created.ssh_url,
            )

    coordinator = ProvisioningCoordinator(
        service=service,  # type: ignore[arg-type]
        repo_provisioner=TrunkRepoProvisioner(),
        channel_provisioner=FakeChannelProvisioner(),
    )

    result = coordinator.provision_agent(
        ProvisionRequest(
            name="payments",
            platform="slack",
            repo_path=None,
            channel_id=None,
            repo_ref="main",
            agent_adapter="codex",
            create_repo=True,
            repo_owner=None,
            repo_name=None,
            repo_visibility="private",
            create_channel=True,
            channel_name=None,
            channel_visibility="private",
        ),
        context=ProvisionContext(platform="slack", admin_channel_id="CADMIN"),
    )

    assert result.ok is True
    assert service.calls[0]["repo_ref"] == "trunk"


def test_provisioning_coordinator_requires_repo_or_create_repo() -> None:
    coordinator = ProvisioningCoordinator(service=FakeService())  # type: ignore[arg-type]
    result = coordinator.provision_agent(
        ProvisionRequest(
            name="payments",
            platform="slack",
            repo_path=None,
            channel_id="C123",
            repo_ref="main",
            agent_adapter=None,
            create_repo=False,
            repo_owner=None,
            repo_name=None,
            repo_visibility="private",
            create_channel=False,
            channel_name=None,
            channel_visibility="private",
        ),
        context=ProvisionContext(platform="slack", admin_channel_id="CADMIN"),
    )
    assert result.ok is False
    assert result.code == "ERR_INVALID_ARGS"


def test_slack_channel_provisioner_creates_private_channel() -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        def conversations_create(self, *, name: str, is_private: bool) -> dict[str, object]:
            calls.append({"name": name, "is_private": is_private})
            return {"channel": {"id": "C777", "name": name}}

    provisioner = SlackChannelProvisioner(FakeClient())
    created = provisioner.create_channel(
        agent_name="payments-api",
        channel_name=None,
        visibility="private",
        context=ProvisionContext(platform="slack", admin_channel_id="CADMIN"),
    )

    assert calls == [{"name": "agent-payments-api", "is_private": True}]
    assert created.channel_id == "C777"
    assert created.channel_name == "agent-payments-api"
