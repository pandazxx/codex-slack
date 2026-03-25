"""Tests for DispatchResult dataclass and codex adapter behaviour.

Covers:
  - DR-01 to DR-06: DispatchResult defaults, field values, and codex notice injection
  - IC-01: route_prompt() return type is DispatchResult on all adapter paths
  - IC-05: adapters satisfy the updated AgentDispatcher Protocol

Spec reference: docs/decisions/0001-rich-file-attachment-support.md
Test plan:      docs/test-plans/rich-file-attachments.md  (sections 3 & 6)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.master.dispatch_result import DispatchResult
from src.master.registry import AgentRecord, AgentRegistry
from src.master.router import (
    ChannelRouter,
    ClaudeCodeDispatcher,
    PodmanExecDispatcher,
    RouteError,
)

# The "not supported" notice text defined in the ADR.
_NOT_SUPPORTED_NOTICE = (
    "File attachments in replies are not supported for this agent type"
)


# ---------------------------------------------------------------------------
# DR-01  DispatchResult defaults
# ---------------------------------------------------------------------------

class TestDispatchResultDefaults:
    def test_file_paths_defaults_to_empty_list(self) -> None:
        """DR-01: constructing DispatchResult with only text leaves file_paths as []."""
        result = DispatchResult(text="hello")
        assert result.file_paths == []

    def test_file_paths_default_is_not_shared_across_instances(self) -> None:
        """Regression: mutable default must not be shared between instances."""
        a = DispatchResult(text="a")
        b = DispatchResult(text="b")
        a.file_paths.append(Path("/tmp/x"))
        assert b.file_paths == []

    def test_text_field_is_stored(self) -> None:
        result = DispatchResult(text="agent reply")
        assert result.text == "agent reply"


# ---------------------------------------------------------------------------
# DR-02  DispatchResult with explicit file_paths
# ---------------------------------------------------------------------------

class TestDispatchResultWithFilePaths:
    def test_file_paths_stores_provided_paths(self) -> None:
        """DR-02: file_paths holds exactly the paths passed at construction."""
        paths = [Path("/workspace/out/report.docx"), Path("/workspace/out/chart.xlsx")]
        result = DispatchResult(text="done", file_paths=paths)
        assert result.file_paths == paths

    def test_file_paths_can_hold_single_path(self) -> None:
        p = Path("/workspace/out/result.pdf")
        result = DispatchResult(text="done", file_paths=[p])
        assert len(result.file_paths) == 1
        assert result.file_paths[0] == p


# ---------------------------------------------------------------------------
# DR-03  Codex adapter (PodmanExecDispatcher) always returns file_paths=[]
# ---------------------------------------------------------------------------

class TestCodexAdapterFilePathsEmpty:
    def test_podman_exec_dispatcher_returns_dispatch_result_with_empty_file_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DR-03: PodmanExecDispatcher.send_prompt() returns DispatchResult with file_paths=[]."""
        dispatcher = PodmanExecDispatcher()

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="agent text", stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert result.file_paths == []
        assert result.text == "agent text"


# ---------------------------------------------------------------------------
# DR-04  ClaudeCodeDispatcher returns DispatchResult with file_paths from JSON
# ---------------------------------------------------------------------------

class TestClaudeCodeDispatcherReturnsDispatchResult:
    def test_send_prompt_returns_dispatch_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DR-04: ClaudeCodeDispatcher.send_prompt() returns DispatchResult."""
        import json

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")

        response_json = json.dumps({
            "result": "The answer is 42.",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.001,
        })

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=response_json, stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="what is the answer?",
            platform="slack",
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert "The answer is 42." in result.text

    def test_send_prompt_extracts_file_paths_from_json_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DR-04: file paths listed in the agent JSON envelope are exposed in file_paths."""
        import json

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")

        response_json = json.dumps({
            "result": "Here is your document.",
            "output_files": ["/workspace/out/report.docx"],
            "usage": {},
        })

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=response_json, stderr="")

        fake_paths = [Path("/workspace/out/report.docx")]

        def fake_retrieve(self, *, raw_stdout: str, container_name: str) -> list[Path]:  # type: ignore[no-untyped-def]
            return fake_paths

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)
        monkeypatch.setattr(ClaudeCodeDispatcher, "_retrieve_output_files", fake_retrieve)

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="generate a report",
            platform="slack",
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert any("report.docx" in str(p) for p in result.file_paths)


# ---------------------------------------------------------------------------
# DR-05  Codex adapter prepends "not supported" notice when prompt had attachments
# ---------------------------------------------------------------------------

class TestCodexNotSupportedNotice:
    def test_codex_prepends_not_supported_notice_when_prompt_contained_attachments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DR-05: when the prompt contains file attachment markers, the codex adapter
        prepends the 'not supported' notice from the ADR."""
        dispatcher = PodmanExecDispatcher()

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="codex reply", stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        # The prompt contains a file attachment marker as injected by the master.
        prompt_with_attachment = "Please review this:\n\n[attachment: report.docx]\nExtracted text here."

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt=prompt_with_attachment,
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert _NOT_SUPPORTED_NOTICE in result.text

    def test_codex_does_not_prepend_notice_for_plain_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DR-05 negative: no attachment marker → no notice prepended."""
        dispatcher = PodmanExecDispatcher()

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="clean reply", stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="just a plain text question",
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert _NOT_SUPPORTED_NOTICE not in result.text


# ---------------------------------------------------------------------------
# DR-06 / IC-01  route_prompt() always returns DispatchResult
# ---------------------------------------------------------------------------

class TestRoutPromptReturnType:
    def _make_router(self, tmp_path: Path, dispatcher: object) -> ChannelRouter:  # type: ignore[no-untyped-def]
        registry = AgentRegistry(tmp_path / "agents.json")
        registry.upsert(
            AgentRecord(
                name="payments-agent",
                repo_path="/tmp/repo",
                channel_id="CAGENT",
                container_name="agent-payments",
                runtime="podman",
                image_plan={"type": "default", "image": "codex-slack-bot:latest"},
                status="running",
            )
        )
        return ChannelRouter(
            registry=registry,
            dispatcher=dispatcher,  # type: ignore[arg-type]
            admin_channels={"CADMIN"},
        )

    def test_route_prompt_returns_dispatch_result_not_str(self, tmp_path: Path) -> None:
        """DR-06 / IC-01: ChannelRouter.route_prompt() returns DispatchResult on all paths."""

        class FakeDispatcher:
            def send_prompt(self, **kwargs: object) -> DispatchResult:  # type: ignore[no-untyped-def]
                return DispatchResult(text="response from agent")

        router = self._make_router(tmp_path, FakeDispatcher())

        result = router.route_prompt(
            channel_id="CAGENT",
            text="hello",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult), (
            f"route_prompt() must return DispatchResult, got {type(result).__name__}"
        )

    def test_route_prompt_dispatch_result_preserves_file_paths(self, tmp_path: Path) -> None:
        """IC-01: file_paths from the dispatcher are preserved through ChannelRouter."""

        class FakeDispatcher:
            def send_prompt(self, **kwargs: object) -> DispatchResult:  # type: ignore[no-untyped-def]
                return DispatchResult(
                    text="here is your file",
                    file_paths=[Path("/workspace/out/result.docx")],
                )

        router = self._make_router(tmp_path, FakeDispatcher())

        result = router.route_prompt(
            channel_id="CAGENT",
            text="generate output",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        assert len(result.file_paths) == 1
        assert "result.docx" in str(result.file_paths[0])


# ---------------------------------------------------------------------------
# IC-02  podman cp called correctly for each output file path
# ---------------------------------------------------------------------------

class TestPodmanCpForOutputFiles:
    def test_podman_cp_called_once_per_output_file_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """IC-02: after send_prompt, podman cp must be invoked for each file in file_paths."""
        import json

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")
        seen_cmds: list[list[str]] = []

        response_json = json.dumps({
            "result": "Done.",
            "output_files": ["/workspace/out/a.docx", "/workspace/out/b.xlsx"],
            "usage": {},
        })

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=response_json, stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="generate two files",
            platform="slack",
            channel_id="CAGENT",
            thread_ts=None,
            user_id="U1",
        )

        assert isinstance(result, DispatchResult)
        # Expect podman cp to be called for each path.
        cp_calls = [cmd for cmd in seen_cmds if len(cmd) >= 2 and cmd[0] == "podman" and cmd[1] == "cp"]
        assert len(cp_calls) == 2, (
            f"Expected 2 'podman cp' calls for 2 output files, got {len(cp_calls)}: {cp_calls}"
        )
        # Each cp call must reference the container name and the respective workspace path.
        cp_targets = [cmd[-1] for cmd in cp_calls]  # last arg is <container>:<path>
        assert any("a.docx" in t for t in cp_targets)
        assert any("b.xlsx" in t for t in cp_targets)


# ---------------------------------------------------------------------------
# IC-03  output_files extracted from inner JSON (correct agent format)
# ---------------------------------------------------------------------------

class TestOutputFilesInnerJsonEnvelope:
    """The agent embeds output_files inside its result text as JSON.

    claude --output-format json produces:
        {"result": "{\"result\": \"Done.\", \"output_files\": [...]}", ...}

    _retrieve_output_files must parse the inner JSON to find the paths.
    _parse_response must return only the human-readable "result" text, not raw JSON.
    """

    def _make_outer_envelope(self, inner_result: str) -> str:
        import json
        return json.dumps({
            "result": inner_result,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.001,
        })

    def test_retrieve_output_files_reads_inner_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """IC-03a: output_files embedded as inner JSON inside result are extracted."""
        import json
        from src.master.router import ClaudeCodeDispatcher

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")

        inner = json.dumps({
            "result": "Here is your report.",
            "output_files": ["/workspace/out/report.txt"],
        })
        outer = self._make_outer_envelope(inner)

        seen_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=outer, stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="test-agent",
            container_name="agent-test",
            prompt="generate a report",
            platform="slack",
            channel_id="C001",
            thread_ts=None,
            user_id="U1",
        )

        cp_calls = [cmd for cmd in seen_cmds if len(cmd) >= 2 and cmd[0] == "podman" and cmd[1] == "cp"]
        assert len(cp_calls) == 1
        assert "report.txt" in cp_calls[0][-1]

    def test_parse_response_strips_inner_json_returns_text(self) -> None:
        """IC-03b: _parse_response returns human-readable text, not raw JSON, when result is inner JSON."""
        import json
        from src.master.router import ClaudeCodeDispatcher

        inner = json.dumps({
            "result": "Here is your report.",
            "output_files": ["/workspace/out/report.txt"],
        })
        outer = json.dumps({
            "result": inner,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.001,
        })

        response = ClaudeCodeDispatcher._parse_response(outer)
        assert "Here is your report." in response
        assert "output_files" not in response
        assert "/workspace" not in response

    def test_parse_response_plain_text_result_unaffected(self) -> None:
        """IC-03c: plain-text result (no inner JSON) is returned as-is — regression guard."""
        import json
        from src.master.router import ClaudeCodeDispatcher

        outer = json.dumps({
            "result": "This is a plain text reply.",
            "usage": {"input_tokens": 5, "output_tokens": 3},
            "total_cost_usd": 0.0005,
        })

        response = ClaudeCodeDispatcher._parse_response(outer)
        assert "This is a plain text reply." in response

    def test_send_prompt_returns_clean_text_not_raw_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IC-03d: end-to-end send_prompt returns human-readable text, not raw inner JSON."""
        import json
        from src.master.router import ClaudeCodeDispatcher

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")

        inner = json.dumps({
            "result": "Summary written successfully.",
            "output_files": ["/workspace/out/summary.txt"],
        })
        outer = self._make_outer_envelope(inner)

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=outer, stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="test-agent",
            container_name="agent-test",
            prompt="write a summary",
            platform="discord",
            channel_id="C002",
            thread_ts=None,
            user_id="U2",
        )

        assert "Summary written successfully." in result.text
        assert "output_files" not in result.text
        assert "/workspace" not in result.text

    def test_retrieve_output_files_inner_json_missing_result_key_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IC-03e: inner JSON without a 'result' key is handled gracefully — no crash."""
        import json
        from src.master.router import ClaudeCodeDispatcher

        dispatcher = ClaudeCodeDispatcher(command_template="claude -p --output-format json")

        # Agent emits only output_files with no result key (malformed but should not crash)
        inner = json.dumps({"output_files": ["/workspace/out/file.txt"]})
        outer = self._make_outer_envelope(inner)

        seen_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=outer, stderr="")

        monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

        result = dispatcher.send_prompt(
            agent_name="test-agent",
            container_name="agent-test",
            prompt="generate file",
            platform="slack",
            channel_id="C003",
            thread_ts=None,
            user_id="U3",
        )

        assert isinstance(result, DispatchResult)
        cp_calls = [cmd for cmd in seen_cmds if len(cmd) >= 2 and cmd[0] == "podman" and cmd[1] == "cp"]
        assert len(cp_calls) == 1
        assert "file.txt" in cp_calls[0][-1]
