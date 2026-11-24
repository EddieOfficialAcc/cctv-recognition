from datetime import datetime, timedelta, timezone
from functools import lru_cache
import logging
from pathlib import Path
import re
from threading import Lock

from PIL import Image
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.recognition_models import RecognitionCheck

logger = logging.getLogger(__name__)
HONG_KONG = timezone(timedelta(hours=8))
_ocr_lock = Lock()


class OCRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCR_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    enabled: bool = False
    drift_seconds: int = Field(default=180, ge=1, le=86400)
    min_confidence: float = Field(default=0.85, ge=0, le=1)


@lru_cache(maxsize=1)
def _engine():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="en_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
    )


def _read_text(image: Image.Image, min_confidence: float) -> str:
    import numpy as np

    crop = image.crop((0, 0, max(1, image.width // 2), max(1, image.height // 5)))
    bgr = np.ascontiguousarray(np.asarray(crop)[:, :, ::-1])
    with _ocr_lock:
        results = _engine().predict(bgr)
    lines = []
    for result in results:
        lines.extend(
            text for text, score in zip(result["rec_texts"], result["rec_scores"])
            if score >= min_confidence
        )
    return " ".join(lines)


_TIMESTAMP = re.compile(
    r"(?<!\d)(\d{4})[-/.](\d{2})[-/.](\d{2})\s+"
    r"(\d{2}):(\d{2})(?::(\d{2}))?(?![\d:])"
    r"(?!\s*(?:AM|PM|UTC|GMT|Z)\b)(?!\s*[+-]\d)",
    re.IGNORECASE,
)


def parse_timestamp(text: str) -> datetime | None:
    matches = list(_TIMESTAMP.finditer(text))
    if len(matches) != 1:
        return None
    year, month, day, hour, minute, second = matches[0].groups()
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute),
            int(second or 0), tzinfo=HONG_KONG,
        )
    except ValueError:
        return None


def check_timestamp(image: Image.Image, captured_at: datetime | None) -> RecognitionCheck:
    settings = OCRSettings()
    if not settings.enabled:
        return RecognitionCheck(
            category="timestamp", status="not_assessed",
            explanation="Timestamp OCR is not configured. Manual verification is required; "
            "upload time must not be used as the camera capture time.",
        )
    if captured_at is None:
        return RecognitionCheck(
            category="timestamp", status="not_assessed",
            explanation="Timestamp comparison requires the actual capture time; "
            "upload time is not a substitute.",
        )
    try:
        text = _read_text(image, settings.min_confidence)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        logger.error("Timestamp OCR failed (%s).", type(error).__name__)
        return RecognitionCheck(
            category="timestamp", status="not_assessed",
            explanation="Timestamp OCR is unavailable or failed. Check the server logs and "
            "verify the camera timestamp manually.",
        )
    camera_time = parse_timestamp(text)
    if camera_time is None:
        return RecognitionCheck(
            category="timestamp", status="not_assessed",
            explanation="No unambiguous supported timestamp was read with sufficient confidence. "
            "Verify the camera timestamp manually.",
        )
    drift = abs((camera_time - captured_at).total_seconds())
    # The upload form records capture time to the minute, so boundary cases are uncertain.
    if abs(drift - settings.drift_seconds) <= 60:
        status = "not_assessed"
        explanation = "Timestamp drift is close to the threshold; verify the exact capture time manually."
    elif drift > settings.drift_seconds:
        status = "flagged"
        explanation = f"Camera timestamp differs from the supplied capture time by approximately {round(drift)} seconds."
    else:
        status = "pass"
        explanation = f"Camera timestamp is within the configured drift threshold (approximately {round(drift)} seconds)."
    return RecognitionCheck(category="timestamp", status=status, explanation=explanation)
