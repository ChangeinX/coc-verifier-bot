from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from tournament_bot.models import (
    BracketMatch,
    BracketSlot,
    BracketState,
    TeamRegistration,
)

from .nlp import contains_token, token_forms
from .ocr import DetectedLine, normalize_text

SCORE_PATTERN = re.compile(r"(\d{1,3})(?:\.\d+)?%?")


@dataclass(slots=True)
class MatchAutomationResult:
    match_id: str
    winner_slot: int
    winner_label: str
    confidence: float
    method: str
    evidence: list[str]
    scores: dict[str, float | None]


@dataclass(slots=True)
class _ProcessedLine:
    original: str
    normalized: str
    tokens: set[str]


@dataclass(slots=True)
class _CompetitorObservation:
    slot_index: int
    label: str
    synonyms: set[str]
    occurrences: int
    best_score: float | None
    evidence: list[str]


def _line_contains_synonym(line: _ProcessedLine, synonyms: set[str]) -> bool:
    normalized = line.normalized
    tokens = line.tokens
    for synonym in synonyms:
        if not synonym:
            continue
        if len(synonym) >= 4 and synonym in normalized:
            return True
        if contains_token(tokens, synonym):
            return True
    return False


def analyze_bracket_matches(
    state: BracketState,
    lines: Sequence[DetectedLine],
    registrations: Sequence[TeamRegistration] | None = None,
) -> list[MatchAutomationResult]:
    processed = [
        _ProcessedLine(
            original=item.content,
            normalized=normalize_text(item.content),
            tokens=set(token_forms(item.content)),
        )
        for item in lines
        if item.content.strip()
    ]
    lookup = {reg.user_id: reg for reg in registrations or []}

    results: list[MatchAutomationResult] = []
    for match in state.all_matches():
        if match.winner_index is not None:
            continue
        observation = _analyze_single_match(match, processed, lookup)
        if observation is not None:
            results.append(observation)
    results.sort(key=lambda result: result.confidence, reverse=True)
    return results


def _analyze_single_match(
    match: BracketMatch,
    lines: Sequence[_ProcessedLine],
    registrations: dict[int, TeamRegistration],
) -> MatchAutomationResult | None:
    slots = [match.competitor_one, match.competitor_two]
    observations: list[_CompetitorObservation] = []

    for index, slot in enumerate(slots):
        if slot.team_label == "BYE" or slot.team_id is None:
            return None
        synonyms = _synonyms_for_slot(slot, registrations.get(slot.team_id))
        observation = _compute_observation(index, slot.team_label, synonyms, lines)
        observations.append(observation)

    if not any(obs.occurrences for obs in observations):
        return None

    method, winner_obs, loser_obs = _decide_winner(observations)
    if winner_obs is None or loser_obs is None:
        return None

    confidence = _calculate_confidence(method, winner_obs, loser_obs)
    evidence = list(dict.fromkeys(winner_obs.evidence + loser_obs.evidence))
    scores = {
        observations[0].label: observations[0].best_score,
        observations[1].label: observations[1].best_score,
    }
    return MatchAutomationResult(
        match_id=match.match_id,
        winner_slot=winner_obs.slot_index,
        winner_label=winner_obs.label,
        confidence=confidence,
        method=method,
        evidence=evidence,
        scores=scores,
    )


def _synonyms_for_slot(
    slot: BracketSlot, registration: TeamRegistration | None
) -> set[str]:
    values: set[str] = {slot.team_label}
    if registration is not None:
        values.add(registration.user_name)
        if registration.team_name:
            values.add(registration.team_name)
        for player in registration.players:
            values.add(player.name)
    normalized: set[str] = set()
    for value in values:
        if not value:
            continue
        base = normalize_text(value)
        if base:
            normalized.add(base)
        forms = token_forms(value)
        for token in forms:
            if not token:
                continue
            if token.isdigit():
                continue
            if len(token) < 3:
                continue
            if not any(char.isalpha() for char in token):
                continue
            normalized.add(token)
    return normalized


def _compute_observation(
    slot_index: int,
    label: str,
    synonyms: set[str],
    lines: Sequence[_ProcessedLine],
) -> _CompetitorObservation:
    occurrences = 0
    evidence: list[str] = []
    best_score: float | None = None

    for idx, line in enumerate(lines):
        if not line.normalized:
            continue
        if not _line_contains_synonym(line, synonyms):
            continue
        occurrences += 1
        evidence.append(line.original)
        score_values = _scores_near_line(lines, idx)
        if score_values:
            top = max(score_values)
            if best_score is None or top > best_score:
                best_score = top

    return _CompetitorObservation(
        slot_index=slot_index,
        label=label,
        synonyms=synonyms,
        occurrences=occurrences,
        best_score=best_score,
        evidence=evidence,
    )


def _scores_near_line(
    lines: Sequence[_ProcessedLine],
    idx: int,
) -> list[float]:
    window = range(max(0, idx - 1), idx + 1)
    values: list[float] = []
    for position in window:
        text = lines[position].original
        for match in SCORE_PATTERN.finditer(text):
            raw_value = match.group(1)
            try:
                value = float(raw_value)
            except ValueError:  # pragma: no cover - defensive
                continue
            if 0 <= value <= 100:
                values.append(value)
    return values


def _decide_winner(
    observations: Sequence[_CompetitorObservation],
) -> tuple[str, _CompetitorObservation | None, _CompetitorObservation | None]:
    if len(observations) != 2:
        return "unsure", None, None

    first, second = observations
    score_one = first.best_score
    score_two = second.best_score

    if (
        score_one is not None
        and score_two is not None
        and not math.isclose(score_one, score_two)
    ):
        if score_one > score_two:
            return "score", first, second
        return "score", second, first

    if score_one is not None and score_two is None:
        return "score-single", first, second
    if score_two is not None and score_one is None:
        return "score-single", second, first

    occ_one = first.occurrences
    occ_two = second.occurrences
    if occ_one > occ_two:
        return "mentions", first, second
    if occ_two > occ_one:
        return "mentions", second, first

    return "unsure", None, None


def _calculate_confidence(
    method: str,
    winner: _CompetitorObservation,
    loser: _CompetitorObservation,
) -> float:
    if method == "score":
        assert winner.best_score is not None and loser.best_score is not None
        diff = abs(winner.best_score - loser.best_score)
        confidence = 0.55 + min(0.4, diff / 150)
    elif method == "score-single":
        # Only one score located. Provide conservative confidence.
        confidence = 0.6
    elif method == "mentions":
        total = max(1, winner.occurrences + loser.occurrences)
        diff = winner.occurrences - loser.occurrences
        confidence = 0.55 + min(0.3, diff / total * 0.3)
    else:
        confidence = 0.5
    return round(min(confidence, 0.95), 3)


__all__ = ["MatchAutomationResult", "analyze_bracket_matches"]
