from __future__ import annotations

import json

from src.master.cli import main


def test_cli_list_empty_registry(tmp_path, capsys) -> None:
    code = main(["--registry", str(tmp_path / "agents.json"), "list"])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["agents"] == []
