from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from tournament_bot.models import BracketState, TeamRegistration

from .analyzer import MatchAutomationResult, analyze_bracket_matches
from .ocr import DetectedLine, OCREngine

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AutomationPreview:
    matches: list[MatchAutomationResult]
    ocr_lines: list[DetectedLine]


def _default_ocr_engine() -> OCREngine:
    textract_client = _safe_textract_client()
    return OCREngine(textract_client=textract_client)


def _safe_textract_client() -> object | None:
    try:
        import boto3
    except ImportError:  # pragma: no cover - environment dependent
        log.info("boto3 not installed; Textract OCR disabled")
        return None
    try:
        return boto3.client("textract")
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Unable to initialize Textract client: %s", exc)
        return None


class MatchAutomationService:
    def __init__(self, *, ocr_engine: OCREngine | None = None) -> None:
        self._ocr = ocr_engine or _default_ocr_engine()

    def analyze_image(
        self,
        bracket: BracketState,
        image_bytes: bytes,
        *,
        registrations: Sequence[TeamRegistration] | None = None,
    ) -> AutomationPreview:
        lines = self._ocr.extract(image_bytes)
        results = analyze_bracket_matches(bracket, lines, registrations)
        return AutomationPreview(matches=results, ocr_lines=lines)


__all__ = ["AutomationPreview", "MatchAutomationService"]
