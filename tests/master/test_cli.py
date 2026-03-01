from __future__ import annotations

import json

from src.master.cli import main


def test_cli_list_empty_registry(tmp_path, capsys) -> None:
    code = main(["--registry", str(tmp_path / "agents.json"), "list"])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["command"] == "list"
    assert payload["request_id"].startswith("req_")
    assert payload["at"]
    assert payload["data"]["agents"] == []
