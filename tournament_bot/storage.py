from __future__ import annotations

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .models import BracketState, TeamRegistration, TournamentConfig


class TournamentStorage:
    def __init__(self, table) -> None:
        self._table = table

    def ensure_table(self) -> None:
        if self._table is None:
            raise RuntimeError("Tournament table is not configured")

    def get_config(self, guild_id: int) -> TournamentConfig | None:
        self.ensure_table()
        resp = self._table.get_item(Key=TournamentConfig.key(guild_id))
        item = resp.get("Item")
        if not item:
            return None
        return TournamentConfig.from_item(item)

    def save_config(self, config: TournamentConfig) -> None:
        self.ensure_table()
        self._table.put_item(Item=config.to_item())

    def get_registration(self, guild_id: int, user_id: int) -> TeamRegistration | None:
        self.ensure_table()
        resp = self._table.get_item(Key=TeamRegistration.key(guild_id, user_id))
        item = resp.get("Item")
        if not item:
            return None
        return TeamRegistration.from_item(item)

    def save_registration(self, registration: TeamRegistration) -> None:
        self.ensure_table()
        self._table.put_item(Item=registration.to_item())

    def list_registrations(self, guild_id: int) -> list[TeamRegistration]:
        self.ensure_table()
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(TeamRegistration.PK_TEMPLATE % guild_id)
            & Key("sk").begins_with("TEAM#"),
            Select="ALL_ATTRIBUTES",
        )
        items = resp.get("Items", [])
        registrations = [TeamRegistration.from_item(item) for item in items]
        registrations.sort(key=lambda entry: (entry.registered_at, entry.user_id))
        return registrations

    def registration_count(self, guild_id: int) -> int:
        self.ensure_table()
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(TeamRegistration.PK_TEMPLATE % guild_id)
            & Key("sk").begins_with("TEAM#"),
            Select="COUNT",
        )
        return int(resp.get("Count", 0))

    def delete_registration(self, guild_id: int, user_id: int) -> bool:
        self.ensure_table()
        try:
            self._table.delete_item(
                Key=TeamRegistration.key(guild_id, user_id),
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as exc:  # pragma: no cover - defensive
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def save_bracket(self, bracket: BracketState) -> None:
        self.ensure_table()
        self._table.put_item(Item=bracket.to_item())

    def get_bracket(self, guild_id: int) -> BracketState | None:
        self.ensure_table()
        resp = self._table.get_item(Key=BracketState.key(guild_id))
        item = resp.get("Item")
        if not item:
            return None
        return BracketState.from_item(item)

    def delete_bracket(self, guild_id: int) -> None:
        self.ensure_table()
        try:
            self._table.delete_item(
                Key=BracketState.key(guild_id),
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as exc:  # pragma: no cover - defensive
            code = exc.response.get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise


__all__ = ["TournamentStorage"]
