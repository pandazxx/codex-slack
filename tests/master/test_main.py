from __future__ import annotations

from src.master.main import mask_token


def test_mask_token_returns_dash_for_empty() -> None:
    assert mask_token("") == "-"
    assert mask_token("   ") == "-"


def test_mask_token_masks_short_tokens_fully() -> None:
    assert mask_token("abcd") == "****"
    assert mask_token("12345678") == "********"


def test_mask_token_keeps_prefix_and_suffix_for_long_tokens() -> None:
    assert mask_token("xoxb-1234567890") == "xoxb...7890"
    assert mask_token("xapp-abcdef123456") == "xapp...3456"
