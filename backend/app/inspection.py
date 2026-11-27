from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.recognition_models import RecognitionCheck
from app.vision_analysis import analyze

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_BURST_FRAME_BYTES = 1024 * 1024
ANALYZER_VERSION = "vision-llm-v2"


class InvalidScreenshot(ValueError):
    pass


@dataclass
class ScreenshotAnalysis:
    evidence: bytes
    width: int
    height: int
    checks: list[RecognitionCheck]
    analyzer_version: str


def _decode_screenshot(content: bytes) -> Image.Image:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise InvalidScreenshot("Upload a non-empty image up to 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise InvalidScreenshot("Only JPEG, PNG and WebP screenshots are supported.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise InvalidScreenshot("Screenshot resolution must not exceed 20 megapixels.")
                if getattr(source, "n_frames", 1) != 1:
                    raise InvalidScreenshot("Upload a single screenshot, not an animated image.")
                source.load()
                return ImageOps.exif_transpose(source).convert("RGB")
    except (
        UnidentifiedImageError, OSError, Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise InvalidScreenshot("The screenshot is corrupt or cannot be decoded safely.") from error


def _jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def prepare_screenshot(content: bytes) -> bytes:
    return _jpeg(_decode_screenshot(content))


def analyze_screenshot(
    content: bytes, captured_at: datetime | None = None, baseline: bytes | None = None,
    *, previous_day: bytes | None = None, daily: bool = False,
) -> ScreenshotAnalysis:
    image = _decode_screenshot(content)
    evidence = content if daily else _jpeg(image)
    return ScreenshotAnalysis(
        evidence=evidence, width=image.width, height=image.height,
        analyzer_version="vision-llm-daily-v1" if daily else ANALYZER_VERSION,
        checks=(analyze(evidence, [], baseline, captured_at, None, previous_day=previous_day, daily=True)
                if daily else analyze(evidence, [], baseline, captured_at, None)),
    )


def analyze_burst(
    first_content: bytes, additional_frames: list[bytes], captured_at: datetime | None = None,
    baseline: bytes | None = None, duration_seconds: float | None = None,
) -> tuple[ScreenshotAnalysis, list[bytes]]:
    if not 5 <= len(additional_frames) <= 9:
        raise InvalidScreenshot("A burst requires 6 to 10 frames including the screenshot.")
    if any(not frame or len(frame) > MAX_BURST_FRAME_BYTES for frame in additional_frames):
        raise InvalidScreenshot("Each additional burst frame must be non-empty and at most 1 MB.")

    first = _decode_screenshot(first_content)
    evidence = []
    for content in additional_frames:
        image = _decode_screenshot(content)
        if image.size != first.size:
            raise InvalidScreenshot("All burst frames must have the same dimensions.")
        evidence.append(_jpeg(image))
    first_evidence = _jpeg(first)
    analysis = ScreenshotAnalysis(
        evidence=first_evidence, width=first.width, height=first.height,
        analyzer_version="vision-llm-burst-v2",
        checks=analyze(first_evidence, evidence, baseline, captured_at, duration_seconds),
    )
    return analysis, evidence
