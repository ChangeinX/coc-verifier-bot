from __future__ import annotations

from match_automation.nlp import contains_token, token_forms


def test_token_forms_handle_special_symbols() -> None:
    forms = token_forms("[K$C -> SAMPARK ]")
    assert "sampark" in forms
    assert "k$c" in forms


def test_token_forms_capture_full_names() -> None:
    forms = token_forms("A¥RA")
    assert "a¥ra" in forms
    assert "ara" in forms


def test_contains_token_matches_normalized_variants() -> None:
    tokens = set(token_forms("AYRA dominates"))
    assert contains_token(tokens, "AYRA")
    assert contains_token(tokens, "ayra")
