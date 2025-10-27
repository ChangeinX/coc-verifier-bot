"""Automation helpers for identifying tournament winners via OCR."""

from .analyzer import MatchAutomationResult, analyze_bracket_matches
from .feedback import FeedbackAttachment, FeedbackRecorder
from .ocr import DetectedLine, OCREngine
from .service import AutomationPreview, MatchAutomationService

__all__ = [
    "AutomationPreview",
    "DetectedLine",
    "MatchAutomationResult",
    "MatchAutomationService",
    "FeedbackAttachment",
    "FeedbackRecorder",
    "OCREngine",
    "analyze_bracket_matches",
]
