from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DetectedLine:
    """Simple representation of OCR output."""

    content: str
    confidence: float | None = None

    def normalized(self) -> str:
        return normalize_text(self.content)


def normalize_text(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


class OCREngine:
    """High-level OCR orchestrator with Textract primary and Tesseract fallback."""

    def __init__(
        self,
        *,
        textract_client: object | None = None,
        tesseract_command: str = "tesseract",
        tesseract_args: Sequence[str] | None = None,
    ) -> None:
        self._textract_client = textract_client
        self._tesseract_command = tesseract_command
        self._tesseract_args = list(tesseract_args or ["--psm", "6"])

    def extract(self, image_bytes: bytes) -> list[DetectedLine]:
        errors: list[str] = []
        lines: list[DetectedLine] | None = None

        if self._textract_client is not None:
            try:
                lines = TextractBackend(self._textract_client).extract(image_bytes)
            except (ClientError, BotoCoreError) as exc:
                log.warning("Textract OCR failed: %s", exc)
                errors.append(f"Textract: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("Unexpected Textract failure")
                errors.append(f"Textract: {exc}")

        if lines is None:
            if shutil.which(self._tesseract_command):
                try:
                    lines = TesseractBackend(
                        self._tesseract_command, self._tesseract_args
                    ).extract(image_bytes)
                except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                    log.warning("Tesseract OCR failed: %s", exc)
                    errors.append(f"Tesseract: {exc}")
                except Exception as exc:  # pragma: no cover - defensive
                    log.exception("Unexpected Tesseract failure")
                    errors.append(f"Tesseract: {exc}")
            else:
                errors.append("Tesseract command not available")

        if lines is None:
            raise RuntimeError(
                "No OCR backend succeeded. ; ".join(errors)
                if errors
                else "No backend configured"
            )
        return lines


class TextractBackend:
    def __init__(self, client: object) -> None:
        self._client = client

    def extract(self, image_bytes: bytes) -> list[DetectedLine]:
        response = self._client.detect_document_text(Document={"Bytes": image_bytes})
        blocks: Iterable[dict[str, object]] = response.get("Blocks", [])  # type: ignore[assignment]
        lines: list[DetectedLine] = []
        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue
            text = block.get("Text")
            if not isinstance(text, str) or not text.strip():
                continue
            confidence = block.get("Confidence")
            confidence_value = (
                float(confidence) if isinstance(confidence, (int, float)) else None
            )
            lines.append(
                DetectedLine(content=text.strip(), confidence=confidence_value)
            )
        return lines


class TesseractBackend:
    def __init__(self, command: str, args: Sequence[str]) -> None:
        self._command = command
        self._args = list(args)

    def extract(self, image_bytes: bytes) -> list[DetectedLine]:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(image_bytes)
            tmp_path = Path(tmp.name)
        try:
            process = subprocess.run(
                [self._command, str(tmp_path), "stdout", *self._args],
                check=True,
                capture_output=True,
            )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        output = process.stdout.decode("utf-8", errors="ignore")
        stderr = process.stderr.decode("utf-8", errors="ignore")
        if stderr.strip():  # pragma: no cover - mostly informational
            log.debug("Tesseract stderr: %s", stderr.strip())

        lines = [
            DetectedLine(content=line.strip() or "", confidence=None)
            for line in output.splitlines()
        ]
        return [line for line in lines if line.content]


__all__ = [
    "DetectedLine",
    "OCREngine",
    "TextractBackend",
    "TesseractBackend",
    "normalize_text",
]
