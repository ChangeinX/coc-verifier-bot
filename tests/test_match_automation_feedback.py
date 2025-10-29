from __future__ import annotations

from decimal import Decimal

from match_automation.feedback import FeedbackAttachment, FeedbackRecorder
from tournament_bot.models import MatchAutomationFeedback


class FakeTable:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put_item(self, Item: dict[str, object]) -> None:  # noqa: N803 (boto style)
        self.items.append(Item)


class FakeS3:
    def __init__(self) -> None:
        self.objects: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs) -> None:  # noqa: N803
        self.objects.append((Bucket, Key))


def _build_feedback(**overrides: object) -> MatchAutomationFeedback:
    data = {
        "guild_id": 1,
        "division_id": "alpha",
        "match_id": "M1",
        "source_channel_id": 10,
        "source_message_id": 20,
        "source_message_url": "https://discord.test",
        "reviewer_id": 30,
        "reviewer_name": "Ref",
        "selected_slot": 1,
        "selected_label": "Winners",
        "predicted_slot": 0,
        "predicted_label": "Losers",
        "predicted_confidence": 0.62,
        "predicted_method": "score",
        "predicted_scores": {"Winners": 80.0},
        "predicted_evidence": ["line one", "line two"],
        "recorded_at": "2024-01-01T00:00:00.000Z",
        "attachments": ["path/file.png"],
        "source_author_id": 99,
        "source_author_name": "Source",
    }
    data.update(overrides)
    return MatchAutomationFeedback(**data)


def test_match_automation_feedback_roundtrip() -> None:
    feedback = _build_feedback()
    item = feedback.to_item()
    assert item["predicted_confidence"] == Decimal("0.62")
    clone = MatchAutomationFeedback.from_item(item)
    assert clone.guild_id == feedback.guild_id
    assert clone.division_id == feedback.division_id
    assert clone.predicted_scores == {"Winners": 80.0}
    assert clone.attachments == ["path/file.png"]
    assert clone.source_author_id == 99


def test_feedback_recorder_disabled_returns_none() -> None:
    recorder = FeedbackRecorder(table=None, s3_client=None, bucket=None)
    result = recorder.record_disagreement(
        guild_id=1,
        division_id="alpha",
        match_id="M1",
        prediction_slot=0,
        prediction_label="Team A",
        prediction_confidence=0.7,
        prediction_method="score",
        prediction_scores={},
        prediction_evidence=[],
        selection_slot=1,
        selection_label="Team B",
        reviewer_id=42,
        reviewer_name="Mod",
        source_channel_id=5,
        source_message_id=10,
        source_message_url="https://discord.test",
        source_author_id=None,
        source_author_name=None,
        attachments=[],
    )
    assert result is None


def test_feedback_recorder_records_and_uploads() -> None:
    table = FakeTable()
    s3 = FakeS3()
    recorder = FeedbackRecorder(
        table=table,
        s3_client=s3,
        bucket="bucket",
        prefix="feedback",
        clock=lambda: "2024-01-01T00:00:00.000Z",
    )
    attachments = [
        FeedbackAttachment(
            filename="winner.png", content_type="image/png", data=b"one"
        ),
        FeedbackAttachment(
            filename="loser image.jpg", content_type="image/jpeg", data=b"two"
        ),
    ]
    evidence = [f"line {idx}" for idx in range(15)]

    result = recorder.record_disagreement(
        guild_id=123,
        division_id="alpha",
        match_id="M2",
        prediction_slot=0,
        prediction_label="Team A",
        prediction_confidence=0.88,
        prediction_method="mentions",
        prediction_scores={"Team A": 88.0, "Team B": None},
        prediction_evidence=evidence,
        selection_slot=1,
        selection_label="Team B",
        reviewer_id=77,
        reviewer_name="Reviewer",
        source_channel_id=55,
        source_message_id=66,
        source_message_url="https://discord.test",
        source_author_id=11,
        source_author_name="Author",
        attachments=attachments,
    )

    assert result is not None
    assert len(table.items) == 1
    stored = table.items[0]
    assert stored["attachments"] == [
        "feedback/123/alpha/M2/2024-01-01T00:00:00.000Z/01-winner.png",
        "feedback/123/alpha/M2/2024-01-01T00:00:00.000Z/02-loser_image.jpg",
    ]
    assert stored["predicted_scores"] == {"Team A": Decimal("88.0")}
    assert len(stored["predicted_evidence"]) == 10
    assert len(s3.objects) == 2
    assert s3.objects[0][0] == "bucket"


def test_feedback_recorder_records_dismissal() -> None:
    table = FakeTable()
    s3 = FakeS3()
    recorder = FeedbackRecorder(
        table=table,
        s3_client=s3,
        bucket="bucket",
        prefix="feedback",
        clock=lambda: "2024-06-01T12:00:00.000Z",
    )

    result = recorder.record_dismissal(
        guild_id=55,
        division_id="beta",
        match_id="M3",
        prediction_slot=0,
        prediction_label="Team X",
        prediction_confidence=0.42,
        prediction_method="ocr",
        prediction_scores={"Team X": 42.0, "Team Y": None},
        prediction_evidence=["line"],
        reviewer_id=101,
        reviewer_name="Reviewer",
        source_channel_id=500,
        source_message_id=600,
        source_message_url="https://discord.test",
        source_author_id=77,
        source_author_name="Author",
        attachments=[],
    )

    assert result is not None
    assert len(table.items) == 1
    stored = table.items[0]
    assert stored["selected_slot"] == -1
    assert stored["selected_label"] == "DISMISSED"
    assert stored["predicted_scores"] == {"Team X": Decimal("42.0")}
