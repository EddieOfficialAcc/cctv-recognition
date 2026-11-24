from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import warnings

from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError

from app.recognition_models import RecognitionCheck
from app.timestamp_ocr import OCRSettings, check_timestamp

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_BURST_FRAME_BYTES = 1024 * 1024
ANALYZER_VERSION = "screenshot-statistics-v1"


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


def _analysis_image(image: Image.Image) -> Image.Image:
    # Exclude the borders, which often contain clocks and channel labels.
    crop = image.crop((
        image.width // 10, image.height // 10,
        image.width - image.width // 10, image.height - image.height // 10,
    )).convert("L")
    return crop


def _jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def analyze_screenshot(content: bytes, captured_at: datetime | None = None) -> ScreenshotAnalysis:
    image = _decode_screenshot(content)
    crop = _analysis_image(image)
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
    return ScreenshotAnalysis(
        evidence=_jpeg(image), width=image.width, height=image.height,
        analyzer_version="screenshot-ocr-v2" if OCRSettings().enabled else ANALYZER_VERSION,
        checks=[
            signal,
            check_timestamp(image, captured_at),
            RecognitionCheck(
                category="scene", status="not_assessed",
                explanation="Scene comparison requires a verified baseline and a comparison "
                "engine. Obstruction and camera movement were not assessed.",
            ),
        ],
    )


def analyze_burst(
    first_content: bytes, additional_frames: list[bytes], captured_at: datetime | None = None,
) -> tuple[ScreenshotAnalysis, list[bytes]]:
    if not 5 <= len(additional_frames) <= 9:
        raise InvalidScreenshot("A burst requires 6 to 10 frames including the screenshot.")
    if any(not frame or len(frame) > MAX_BURST_FRAME_BYTES for frame in additional_frames):
        raise InvalidScreenshot("Each additional burst frame must be non-empty and at most 1 MB.")

    analysis = analyze_screenshot(first_content, captured_at)
    first = _decode_screenshot(first_content)
    thumbnails = [_analysis_image(first).resize((160, 90))]
    evidence = []
    for content in additional_frames:
        image = _decode_screenshot(content)
        if image.size != first.size:
            raise InvalidScreenshot("All burst frames must have the same dimensions.")
        thumbnails.append(_analysis_image(image).resize((160, 90)))
        evidence.append(_jpeg(image))

    differences = [
        ImageStat.Stat(ImageChops.difference(left, right)).mean[0]
        for left, right in zip(thumbnails, thumbnails[1:])
    ]
    luminances = [ImageStat.Stat(frame).mean[0] for frame in thumbnails]
    deltas = [right - left for left, right in zip(luminances, luminances[1:])]
    repeated = all(delta < 0.3 for delta in differences)
    flicker = all(abs(delta) >= 20 for delta in deltas) and all(
        left * right < 0 for left, right in zip(deltas, deltas[1:])
    )
    if analysis.checks[0].status == "flagged":
        analysis.checks[0].explanation += " Confirm the burst and live feed manually."
    elif repeated:
        analysis.checks[0] = RecognitionCheck(
            category="signal", status="flagged",
            explanation="Possible frozen or repeated frames. A stationary scene can look similar; verify the live feed.",
        )
    elif flicker:
        analysis.checks[0] = RecognitionCheck(
            category="signal", status="flagged",
            explanation="Possible brightness flicker detected across the burst. Verify the live feed.",
        )
    else:
        analysis.checks[0] = RecognitionCheck(
            category="signal", status=analysis.checks[0].status,
            explanation="No black/blank, repeated-frame or flicker pattern was detected in this burst. "
            "No-signal overlays, freeze and flicker are not ruled out.",
        )
    analysis.analyzer_version = "burst-statistics-v1"
    return analysis, evidence
