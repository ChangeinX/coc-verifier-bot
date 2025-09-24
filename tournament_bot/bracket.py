from __future__ import annotations

from collections.abc import Sequence
from math import log2

from .models import (
    BracketMatch,
    BracketRound,
    BracketSlot,
    BracketState,
    TeamRegistration,
    utc_now_iso,
)


def _next_power_of_two(value: int) -> int:
    if value <= 0:
        raise ValueError("Value must be positive")
    return 1 << (value - 1).bit_length()


def _seed_order(slots: int) -> list[int]:
    if slots == 1:
        return [1]
    if slots == 2:
        return [1, 2]
    half = _seed_order(slots // 2)
    expanded: list[int] = []
    for seed in half:
        expanded.extend([seed, slots + 1 - seed])
    return expanded


def _round_name(round_index: int, total_rounds: int) -> str:
    remaining = total_rounds - round_index
    if remaining == 1:
        return "Final"
    if remaining == 2:
        return "Semifinals"
    if remaining == 3:
        return "Quarterfinals"
    return f"Round of {2**remaining}"


def _build_slot_from_registration(
    registration: TeamRegistration | None, seed: int | None
) -> BracketSlot:
    if registration is None:
        return BracketSlot(seed=None, team_id=None, team_label="BYE")
    label_source = registration.team_name or registration.user_name
    label = label_source.strip() if label_source else "Unnamed Team"
    if not label:
        label = "Unnamed Team"
    return BracketSlot(seed=seed, team_id=registration.user_id, team_label=label)


def _hydrate_sources(state: BracketState) -> bool:
    updated = False
    for match in state.all_matches():
        for competitor in (match.competitor_one, match.competitor_two):
            if competitor.team_id is not None:
                continue
            if competitor.source_match_id is None:
                continue
            source = state.find_match(competitor.source_match_id)
            if source is None:
                continue
            winner = source.winner_slot()
            if winner is None or winner.team_id is None:
                continue
            competitor.adopt_from(winner)
            updated = True
    return updated


def _propagate_winner(state: BracketState, match: BracketMatch) -> None:
    winner = match.winner_slot()
    if winner is None or winner.team_id is None:
        return
    for downstream in state.all_matches():
        for slot in (downstream.competitor_one, downstream.competitor_two):
            if slot.source_match_id == match.match_id:
                slot.adopt_from(winner)


def _assign_winner(state: BracketState, match: BracketMatch, winner_index: int) -> None:
    if winner_index not in (0, 1):
        raise ValueError("winner_index must be 0 or 1")
    competitor = (match.competitor_one, match.competitor_two)[winner_index]
    if competitor.team_id is None:
        raise ValueError("Selected competitor is not ready")
    if match.winner_index is not None and match.winner_index != winner_index:
        raise ValueError("Match already has a different winner recorded")
    match.winner_index = winner_index
    _propagate_winner(state, match)


def _auto_resolve(state: BracketState) -> None:
    while True:
        updated = _hydrate_sources(state)
        auto_assigned = False
        for match in state.all_matches():
            if match.winner_index is not None:
                continue
            comp_one_ready = match.competitor_one.team_id is not None
            comp_two_ready = match.competitor_two.team_id is not None
            if (
                comp_one_ready
                and not comp_two_ready
                and match.competitor_two.source_match_id is None
            ):
                _assign_winner(state, match, 0)
                auto_assigned = True
            elif (
                comp_two_ready
                and not comp_one_ready
                and match.competitor_one.source_match_id is None
            ):
                _assign_winner(state, match, 1)
                auto_assigned = True
        if not updated and not auto_assigned:
            break


def create_bracket_state(
    guild_id: int, registrations: Sequence[TeamRegistration]
) -> BracketState:
    if len(registrations) < 2:
        raise ValueError("At least two teams are required to create a bracket")

    slots = _next_power_of_two(len(registrations))
    seed_order = _seed_order(slots)
    ordered_regs = sorted(
        registrations, key=lambda entry: (entry.registered_at, entry.user_id)
    )
    seed_lookup = {
        seed: ordered_regs[seed - 1] if seed - 1 < len(ordered_regs) else None
        for seed in range(1, slots + 1)
    }

    total_rounds = int(log2(slots))
    rounds: list[BracketRound] = []

    first_round_slots: list[BracketSlot] = []
    for seed in seed_order:
        registration = seed_lookup.get(seed)
        slot_seed = seed if registration is not None else None
        first_round_slots.append(_build_slot_from_registration(registration, slot_seed))

    matches: list[BracketMatch] = []
    for index in range(0, len(first_round_slots), 2):
        match_id = f"R1M{index // 2 + 1}"
        matches.append(
            BracketMatch(
                match_id=match_id,
                round_index=0,
                competitor_one=first_round_slots[index],
                competitor_two=first_round_slots[index + 1],
            )
        )
    rounds.append(BracketRound(name=_round_name(0, total_rounds), matches=matches))

    previous_round_matches = matches
    for round_idx in range(1, total_rounds):
        slots_from_prev = []
        for match in previous_round_matches:
            slots_from_prev.append(
                BracketSlot(
                    seed=None,
                    team_id=None,
                    team_label=f"Winner {match.match_id}",
                    source_match_id=match.match_id,
                )
            )
        next_round_matches: list[BracketMatch] = []
        for index in range(0, len(slots_from_prev), 2):
            match_id = f"R{round_idx + 1}M{index // 2 + 1}"
            next_round_matches.append(
                BracketMatch(
                    match_id=match_id,
                    round_index=round_idx,
                    competitor_one=slots_from_prev[index],
                    competitor_two=slots_from_prev[index + 1],
                )
            )
        rounds.append(
            BracketRound(
                name=_round_name(round_idx, total_rounds), matches=next_round_matches
            )
        )
        previous_round_matches = next_round_matches

    state = BracketState(guild_id=guild_id, created_at=utc_now_iso(), rounds=rounds)
    _auto_resolve(state)
    return state


def set_match_winner(state: BracketState, match_id: str, winner_slot: int) -> None:
    if winner_slot not in (0, 1, 2):
        raise ValueError("winner_slot must be 0/1/2")
    normalized_map = {0: 0, 1: 0, 2: 1}
    normalized = normalized_map[winner_slot]
    _auto_resolve(state)
    match = state.find_match(match_id)
    if match is None:
        raise ValueError(f"Match {match_id} not found")
    _assign_winner(state, match, normalized)
    _auto_resolve(state)


def render_bracket(state: BracketState, *, shrink_completed: bool = False) -> str:
    start_index = 0
    if shrink_completed and state.rounds:
        last_index = len(state.rounds) - 1
        for idx, round_ in enumerate(state.rounds):
            if any(match.winner_index is None for match in round_.matches):
                start_index = idx
                break
        else:
            start_index = last_index

    lines: list[str] = []
    for round_ in state.rounds[start_index:]:
        lines.append(round_.name)
        for match in round_.matches:
            competitor_one = match.competitor_one.display()
            competitor_two = match.competitor_two.display()
            lines.append(f"  [{match.match_id}] {competitor_one} vs {competitor_two}")
            winner = match.winner_slot()
            if winner is not None and winner.team_id is not None:
                lines.append(f"    -> Winner: {winner.display()}")
            else:
                lines.append("    -> Winner: TBD")
        lines.append("")
    if lines and not lines[-1]:
        lines.pop()
    final_round = state.rounds[-1] if state.rounds else None
    champion: str | None = None
    if final_round and final_round.matches:
        winner_slot = final_round.matches[-1].winner_slot()
        if winner_slot and winner_slot.team_id is not None:
            champion = winner_slot.display()
    if champion:
        lines.append(f"Champion: {champion}")
    return "\n".join(line.rstrip() for line in lines)


def team_captain_lines(
    state: BracketState, registrations: Sequence[TeamRegistration]
) -> list[str]:
    """Return formatted lines pairing seeded teams with their captains."""

    registration_lookup = {
        registration.user_id: registration for registration in registrations
    }

    slot_by_team: dict[int, BracketSlot] = {}
    for match in state.all_matches():
        for slot in (match.competitor_one, match.competitor_two):
            if slot.team_id is None:
                continue
            existing = slot_by_team.get(slot.team_id)
            if existing is None:
                slot_by_team[slot.team_id] = slot
                continue
            if existing.seed is None and slot.seed is not None:
                slot_by_team[slot.team_id] = slot

    def sort_key(slot: BracketSlot) -> tuple[int, str]:
        seed = slot.seed if slot.seed is not None else 1_000_000
        return (seed, slot.team_label.lower())

    lines: list[str] = []
    for slot in sorted(slot_by_team.values(), key=sort_key):
        registration = registration_lookup.get(slot.team_id)
        captain = (
            registration.user_name if registration is not None else "Unknown captain"
        )
        lines.append(f"{slot.display()} — Captain: {captain}")
    return lines


def simulate_tournament(
    state: BracketState,
) -> tuple[BracketState, list[tuple[str, BracketState]]]:
    working = state.clone()
    _auto_resolve(working)
    snapshots: list[tuple[str, BracketState]] = [("Initial Bracket", working.clone())]
    for round_ in working.rounds:
        for match in round_.matches:
            if match.winner_index is not None:
                continue
            _auto_resolve(working)
            comp_one_ready = match.competitor_one.team_id is not None
            comp_two_ready = match.competitor_two.team_id is not None
            if not comp_one_ready and not comp_two_ready:
                continue
            if comp_one_ready and not comp_two_ready:
                _assign_winner(working, match, 0)
            elif comp_two_ready and not comp_one_ready:
                _assign_winner(working, match, 1)
            else:
                seed_one = match.competitor_one.seed or 999
                seed_two = match.competitor_two.seed or 999
                winner_index = 0 if seed_one <= seed_two else 1
                _assign_winner(working, match, winner_index)
            _auto_resolve(working)
        snapshots.append((f"After {round_.name}", working.clone()))
    return working, snapshots


__all__ = [
    "create_bracket_state",
    "render_bracket",
    "team_captain_lines",
    "set_match_winner",
    "simulate_tournament",
]
