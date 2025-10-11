from __future__ import annotations

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .models import BracketState, TeamRegistration, TournamentConfig, TournamentSeries


class TournamentStorage:
    def __init__(self, table) -> None:
        self._table = table

    def ensure_table(self) -> None:
        if self._table is None:
            raise RuntimeError("Tournament table is not configured")

    # ----- Series (guild-wide) -----
    def get_series(self, guild_id: int) -> TournamentSeries | None:
        self.ensure_table()
        resp = self._table.get_item(Key=TournamentSeries.key(guild_id))
        item = resp.get("Item")
        if not item:
            return None
        return TournamentSeries.from_item(item)

    def save_series(self, series: TournamentSeries) -> None:
        self.ensure_table()
        self._table.put_item(Item=series.to_item())

    # ----- Division Configurations -----
    def get_config(self, guild_id: int, division_id: str) -> TournamentConfig | None:
        self.ensure_table()
        resp = self._table.get_item(Key=TournamentConfig.key(guild_id, division_id))
        item = resp.get("Item")
        if not item:
            return None
        return TournamentConfig.from_item(item)

    def save_config(self, config: TournamentConfig) -> None:
        self.ensure_table()
        self._table.put_item(Item=config.to_item())

    def list_division_configs(self, guild_id: int) -> list[TournamentConfig]:
        self.ensure_table()
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(TournamentConfig.PK_TEMPLATE % guild_id)
            & Key("sk").begins_with("DIVISION#"),
            Select="ALL_ATTRIBUTES",
        )
        items = resp.get("Items", [])
        configs = [
            TournamentConfig.from_item(item)
            for item in items
            if str(item.get("sk", "")).endswith("#CONFIG")
        ]
        configs.sort(key=lambda cfg: (cfg.division_name.lower(), cfg.division_id))
        return configs

    def list_division_ids(self, guild_id: int) -> list[str]:
        return [cfg.division_id for cfg in self.list_division_configs(guild_id)]

    # ----- Registrations -----
    def get_registration(
        self, guild_id: int, division_id: str, user_id: int
    ) -> TeamRegistration | None:
        self.ensure_table()
        resp = self._table.get_item(
            Key=TeamRegistration.key(guild_id, division_id, user_id)
        )
        item = resp.get("Item")
        if not item:
            return None
        return TeamRegistration.from_item(item)

    def save_registration(self, registration: TeamRegistration) -> None:
        self.ensure_table()
        self._table.put_item(Item=registration.to_item())

    def list_registrations(
        self, guild_id: int, division_id: str
    ) -> list[TeamRegistration]:
        self.ensure_table()
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(TeamRegistration.PK_TEMPLATE % guild_id)
            & Key("sk").begins_with(f"DIVISION#{division_id}#TEAM#"),
            Select="ALL_ATTRIBUTES",
        )
        items = resp.get("Items", [])
        registrations = [TeamRegistration.from_item(item) for item in items]
        registrations.sort(key=lambda entry: (entry.registered_at, entry.user_id))
        return registrations

    def registration_count(self, guild_id: int, division_id: str) -> int:
        self.ensure_table()
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(TeamRegistration.PK_TEMPLATE % guild_id)
            & Key("sk").begins_with(f"DIVISION#{division_id}#TEAM#"),
            Select="COUNT",
        )
        return int(resp.get("Count", 0))

    def delete_registration(
        self, guild_id: int, division_id: str, user_id: int
    ) -> bool:
        self.ensure_table()
        try:
            self._table.delete_item(
                Key=TeamRegistration.key(guild_id, division_id, user_id),
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as exc:  # pragma: no cover - defensive
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def delete_registrations_for_division(self, guild_id: int, division_id: str) -> int:
        registrations = self.list_registrations(guild_id, division_id)
        for registration in registrations:
            self._table.delete_item(
                Key=TeamRegistration.key(
                    registration.guild_id,
                    registration.division_id,
                    registration.user_id,
                ),
                ConditionExpression="attribute_exists(pk)",
            )
        return len(registrations)

    # ----- Brackets -----
    def save_bracket(self, bracket: BracketState) -> None:
        self.ensure_table()
        self._table.put_item(Item=bracket.to_item())

    def get_bracket(self, guild_id: int, division_id: str) -> BracketState | None:
        self.ensure_table()
        resp = self._table.get_item(Key=BracketState.key(guild_id, division_id))
        item = resp.get("Item")
        if not item:
            return None
        return BracketState.from_item(item)

    def delete_bracket(self, guild_id: int, division_id: str) -> None:
        self.ensure_table()
        try:
            self._table.delete_item(
                Key=BracketState.key(guild_id, division_id),
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as exc:  # pragma: no cover - defensive
            code = exc.response.get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise


__all__ = ["TournamentStorage"]
