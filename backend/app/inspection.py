from dataclasses import dataclass
from io import BytesIO
import warnings

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

from app.recognition_models import RecognitionCheck

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ANALYZER_VERSION = "screenshot-statistics-v1"


class InvalidScreenshot(ValueError):
    pass


@dataclass
class ScreenshotAnalysis:
    evidence: bytes
    width: int
    height: int
    checks: list[RecognitionCheck]


def analyze_screenshot(content: bytes) -> ScreenshotAnalysis:
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
                image = ImageOps.exif_transpose(source).convert("RGB")
    except (
        UnidentifiedImageError, OSError, Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise InvalidScreenshot("The screenshot is corrupt or cannot be decoded safely.") from error

    # Exclude timestamp borders from the coarse blank-feed check.
    crop = image.crop((
        image.width // 10, image.height // 10,
        image.width - image.width // 10, image.height - image.height // 10,
    )).convert("L")
    stats = ImageStat.Stat(crop)
    mean, deviation = stats.mean[0], stats.stddev[0]
    flagged = (mean < 12 and deviation < 8) or deviation < 2
    signal = RecognitionCheck(
        category="signal",
        status="flagged" if flagged else "pass",
        explanation=(
            "Possible black or blank feed. Confirm against the original camera; "
            "an unlit scene can look similar."
            if flagged else
            "No black/blank pattern detected in this screenshot. No-signal text "
            "overlays, frozen video and flicker were not verified."
        ),
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return ScreenshotAnalysis(
        evidence=buffer.getvalue(), width=image.width, height=image.height,
        checks=[
            signal,
            RecognitionCheck(
                category="timestamp", status="not_assessed",
                explanation="Timestamp OCR is not configured. Manual verification is required; "
                "upload time must not be used as the camera capture time.",
            ),
            RecognitionCheck(
                category="scene", status="not_assessed",
                explanation="Scene comparison requires a verified baseline and a comparison "
                "engine. Obstruction and camera movement were not assessed.",
            ),
        ],
    )
