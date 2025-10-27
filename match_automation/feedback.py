from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from tournament_bot.models import MatchAutomationFeedback, utc_now_iso

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FeedbackAttachment:
    filename: str
    content_type: str | None
    data: bytes


class FeedbackRecorder:
    def __init__(
        self,
        *,
        table,
        s3_client,
        bucket: str | None,
        prefix: str = "",
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._table = table
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self._table and self._s3 and self._bucket)

    def record_disagreement(
        self,
        *,
        guild_id: int,
        division_id: str,
        match_id: str,
        prediction_slot: int,
        prediction_label: str,
        prediction_confidence: float,
        prediction_method: str,
        prediction_scores: dict[str, float | None],
        prediction_evidence: Sequence[str],
        selection_slot: int,
        selection_label: str,
        reviewer_id: int,
        reviewer_name: str,
        source_channel_id: int,
        source_message_id: int,
        source_message_url: str,
        source_author_id: int | None,
        source_author_name: str | None,
        attachments: Sequence[FeedbackAttachment],
    ) -> MatchAutomationFeedback | None:
        if not self.enabled:
            return None

        timestamp = self._clock()
        uploaded_keys = self._upload_attachments(
            attachments,
            guild_id=guild_id,
            division_id=division_id,
            match_id=match_id,
            timestamp=timestamp,
        )

        feedback = MatchAutomationFeedback(
            guild_id=guild_id,
            division_id=division_id,
            match_id=match_id,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            source_message_url=source_message_url,
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            selected_slot=selection_slot,
            selected_label=selection_label,
            predicted_slot=prediction_slot,
            predicted_label=prediction_label,
            predicted_confidence=prediction_confidence,
            predicted_method=prediction_method,
            predicted_scores={
                label: score
                for label, score in prediction_scores.items()
                if score is not None
            },
            predicted_evidence=list(prediction_evidence)[:10],
            recorded_at=timestamp,
            attachments=uploaded_keys,
            source_author_id=source_author_id,
            source_author_name=source_author_name,
        )

        try:
            self._table.put_item(Item=feedback.to_item())
        except (
            BotoCoreError,
            ClientError,
        ) as exc:  # pragma: no cover - network failure
            log.warning(
                "Failed to store OCR feedback for guild=%s division=%s match=%s: %s",
                guild_id,
                division_id,
                match_id,
                exc,
            )
            return None
        return feedback

    def _upload_attachments(
        self,
        attachments: Sequence[FeedbackAttachment],
        *,
        guild_id: int,
        division_id: str,
        match_id: str,
        timestamp: str,
    ) -> list[str]:
        if not attachments:
            return []
        if not self._bucket or not self._s3:
            return []
        path_parts = [
            part
            for part in [self._prefix, str(guild_id), division_id, match_id, timestamp]
            if part
        ]
        base_path = "/".join(path_parts) if path_parts else "feedback"
        stored_keys: list[str] = []
        for index, attachment in enumerate(attachments, start=1):
            if not attachment.data:
                continue
            safe_name = self._sanitize_filename(attachment.filename)
            key = f"{base_path}/{index:02d}-{safe_name}"
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=attachment.data,
                    ContentType=attachment.content_type or "application/octet-stream",
                    Metadata={
                        "guild_id": str(guild_id),
                        "division_id": division_id,
                        "match_id": match_id,
                    },
                )
            except (
                BotoCoreError,
                ClientError,
            ) as exc:  # pragma: no cover - network failure
                log.warning(
                    "Failed to upload OCR feedback attachment %s for match %s: %s",
                    attachment.filename,
                    match_id,
                    exc,
                )
                continue
            stored_keys.append(key)
        return stored_keys

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        base = filename or "attachment"
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
        return cleaned[:120] or "attachment"
