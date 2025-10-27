from __future__ import annotations

import shutil

import pytest

from match_automation.analyzer import analyze_bracket_matches
from match_automation.ocr import DetectedLine, TextractBackend
from match_automation.service import MatchAutomationService
from tournament_bot.bracket import create_bracket_state
from tournament_bot.models import PlayerEntry, TeamRegistration, utc_now_iso


@pytest.fixture()
def simple_bracket() -> tuple:
    base_time = utc_now_iso()
    registrations = [
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=101,
            user_name="vraj2",
            team_name="vraj2",
            players=[PlayerEntry(name="vraj2", tag="#AAA", town_hall=16)],
            registered_at=base_time,
        ),
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=202,
            user_name="RUSHER X",
            team_name="RUSHER X",
            players=[PlayerEntry(name="RUSHER X", tag="#BBB", town_hall=16)],
            registered_at=base_time,
        ),
    ]
    bracket = create_bracket_state(1, "solo", registrations)
    return bracket, registrations


def test_analyzer_prefers_higher_score(simple_bracket) -> None:
    bracket, registrations = simple_bracket
    lines = [
        DetectedLine(content="Round One", confidence=0.9),
        DetectedLine(content="vraj2 wk 54%", confidence=0.9),
        DetectedLine(content="RUSHER X wk 88%", confidence=0.95),
    ]
    results = analyze_bracket_matches(bracket, lines, registrations)
    assert results, "Expected the analyzer to yield at least one match"
    result = results[0]
    assert result.winner_label == "RUSHER X"
    assert result.confidence >= 0.6
    assert result.method.startswith("score")


def test_analyzer_uses_mentions_when_scores_missing(simple_bracket) -> None:
    bracket, registrations = simple_bracket
    lines = [
        DetectedLine(content="vraj2 dominates", confidence=0.8),
        DetectedLine(content="vraj2 unstoppable", confidence=0.8),
        DetectedLine(content="RUSHER X", confidence=0.8),
    ]
    results = analyze_bracket_matches(bracket, lines, registrations)
    assert results
    result = results[0]
    assert result.winner_label == "vraj2"
    assert result.method == "mentions"


def test_textract_backend_extracts_lines() -> None:
    fake_client_calls: list[dict] = []

    class _FakeTextract:
        def detect_document_text(
            self, *, Document: dict[str, bytes]
        ) -> dict[str, object]:
            fake_client_calls.append(Document)
            return {
                "Blocks": [
                    {"BlockType": "LINE", "Text": "Alpha", "Confidence": 98.0},
                    {"BlockType": "WORD", "Text": "ignored", "Confidence": 0},
                    {"BlockType": "LINE", "Text": "Beta", "Confidence": 87.0},
                ]
            }

    backend = TextractBackend(_FakeTextract())
    data = backend.extract(b"raw-bytes")
    assert [line.content for line in data] == ["Alpha", "Beta"]
    assert len(fake_client_calls) == 1
    assert fake_client_calls[0] == {"Bytes": b"raw-bytes"}


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI is required for this integration test",
)
def test_match_automation_service_reads_real_image(simple_bracket) -> None:
    bracket, registrations = simple_bracket
    service = MatchAutomationService()
    image_path = "match_automation/test_images/Screenshot_2025-10-26-20-27-18-975_com.supercell.clashofclans.jpg"
    with open(image_path, "rb") as handle:
        image_bytes = handle.read()
    preview = service.analyze_image(bracket, image_bytes, registrations=registrations)
    assert preview.ocr_lines, "Expected OCR to return at least one line"
    # We only assert that automation runs; accuracy is validated separately with unit tests.
    assert isinstance(preview.matches, list)


def test_analyzer_ignores_short_synonym_false_positive() -> None:
    base_time = utc_now_iso()
    registrations = [
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=111,
            user_name="[K$C -> SAMPARK ]",
            team_name="[K$C -> SAMPARK ]",
            players=[PlayerEntry(name="Leader", tag="#AAA", town_hall=16)],
            registered_at=base_time,
        ),
        TeamRegistration(
            guild_id=1,
            division_id="solo",
            user_id=222,
            user_name="nova",
            team_name="nova",
            players=[PlayerEntry(name="Nova", tag="#BBB", town_hall=16)],
            registered_at=base_time,
        ),
    ]
    bracket = create_bracket_state(1, "solo", registrations)
    lines = [
        DetectedLine(content="Clan Message", confidence=0.9),
        DetectedLine(content="FCs 22%", confidence=0.9),
        DetectedLine(content="10%", confidence=0.9),
    ]
    results = analyze_bracket_matches(bracket, lines, registrations)
    assert results == []


def _build_registration(
    user_id: int,
    name: str,
    *,
    player_name: str,
) -> TeamRegistration:
    base_time = utc_now_iso()
    return TeamRegistration(
        guild_id=1,
        division_id="solo",
        user_id=user_id,
        user_name=name,
        team_name=name,
        players=[PlayerEntry(name=player_name, tag="#AAA", town_hall=16)],
        registered_at=base_time,
    )


def test_analyzer_ignores_noise_tokens_for_special_char_names() -> None:
    ayra = _build_registration(111, "AYRA", player_name="A¥RA")
    opponent = _build_registration(222, "ah not him", player_name="D⭕️rt")
    bracket = create_bracket_state(1, "solo", [ayra, opponent])
    lines = [
        DetectedLine(content="10m left", confidence=0.8),
        DetectedLine(content="Leader online", confidence=0.8),
        DetectedLine(content="3m Challenge", confidence=0.8),
        DetectedLine(content="69%", confidence=0.8),
    ]
    results = analyze_bracket_matches(bracket, lines, [ayra, opponent])
    assert results == []


def test_analyzer_detects_special_char_names_when_present() -> None:
    ayra = _build_registration(111, "AYRA", player_name="A¥RA")
    opponent = _build_registration(222, "ah not him", player_name="D⭕️rt")
    bracket = create_bracket_state(1, "solo", [ayra, opponent])
    lines = [
        DetectedLine(content="ah not him 64%", confidence=0.9),
        DetectedLine(content="AYRA 92%", confidence=0.9),
    ]
    results = analyze_bracket_matches(bracket, lines, [ayra, opponent])
    assert results
    result = results[0]
    assert result.winner_label == "AYRA"
    assert result.method == "score"


def test_analyzer_links_score_on_following_line(simple_bracket) -> None:
    bracket, registrations = simple_bracket
    lines = [
        DetectedLine(content="RUSHER X", confidence=0.9),
        DetectedLine(content="88%", confidence=0.9),
        DetectedLine(content="vraj2", confidence=0.9),
        DetectedLine(content="54%", confidence=0.9),
    ]
    results = analyze_bracket_matches(bracket, lines, registrations)
    assert results
    result = results[0]
    assert result.winner_label == "RUSHER X"
    assert result.method == "score"


def test_analyzer_prefers_attacker_in_textract_layout(simple_bracket) -> None:
    attacker = TeamRegistration(
        guild_id=1,
        division_id="solo",
        user_id=1,
        user_name="SanD BoX",
        team_name="SanD BoX",
        players=[PlayerEntry(name="SanD BoX", tag="#AAA", town_hall=16)],
        registered_at=utc_now_iso(),
    )
    defender = TeamRegistration(
        guild_id=1,
        division_id="solo",
        user_id=2,
        user_name="Indrathereal",
        team_name="Indrathereal",
        players=[PlayerEntry(name="Indrathereal", tag="#BBB", town_hall=16)],
        registered_at=utc_now_iso(),
    )
    registrations = [attacker, defender]
    bracket = create_bracket_state(1, "solo", registrations)
    lines = [
        DetectedLine(content="SanD BoX", confidence=0.9),
        DetectedLine(content="Indrathereal", confidence=0.9),
        DetectedLine(content="100%", confidence=0.9),
        DetectedLine(content="Replay", confidence=0.9),
        DetectedLine(content="Indrathereal", confidence=0.9),
        DetectedLine(content="SanD BoX", confidence=0.9),
        DetectedLine(content="74%", confidence=0.9),
        DetectedLine(content="Replay", confidence=0.9),
    ]
    results = analyze_bracket_matches(bracket, lines, registrations)
    assert results
    result = results[0]
    assert result.winner_label == "SanD BoX"
    assert result.scores["SanD BoX"] == 100.0
    assert result.scores["Indrathereal"] == 74.0
