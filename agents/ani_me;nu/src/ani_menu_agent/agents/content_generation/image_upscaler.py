"""Guarded, image-model super-resolution for source artwork that will not fit a card."""

from __future__ import annotations

import base64
import io
from time import perf_counter

from openai import OpenAI
from PIL import Image, ImageChops, ImageOps, ImageStat

from ani_menu_agent.models import ImageAsset, UsageRecord

_MAX_ASPECT_RATIO_DRIFT = 0.06
_MAX_DOWNSCALED_RMS = 68.0
_MAX_OUTPUT_BYTES = 20 * 1024 * 1024
_PROMPT = """Perform faithful super-resolution on the supplied anime artwork.
Keep the complete original composition, aspect ratio, characters, faces, poses, outlines,
colors, logos, lettering and all visible details unchanged. Recover clarity in existing
linework only. Do not crop, extend, redraw, reinterpret, replace, add or remove anything.
This is restoration of the same image, not a new illustration. Return the full image at
the highest practical resolution, preserving its original aspect ratio."""


class UpscaleRejected(ValueError):
    """A returned enhancement failed the preservation guard; includes billable usage."""

    def __init__(self, reason: str, usage: UsageRecord) -> None:
        super().__init__(reason)
        self.usage = usage


def _source_bytes(image: ImageAsset, get_image_bytes) -> bytes | None:
    if image.content_bytes:
        return image.content_bytes
    if image.storage_key:
        stored = get_image_bytes(image.storage_key)
        if stored:
            return stored[0]
    if image.local_path:
        from pathlib import Path

        path = Path(image.local_path)
        if path.is_file():
            return path.read_bytes()
    return None


def _verify_result(source_bytes: bytes, result_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(source_bytes)) as original_file:
        original = ImageOps.exif_transpose(original_file).convert("RGB")
    with Image.open(io.BytesIO(result_bytes)) as result_file:
        result = ImageOps.exif_transpose(result_file).convert("RGB")

    if result.width <= original.width or result.height <= original.height:
        raise ValueError("resolution_not_increased")
    original_ratio = original.width / original.height
    result_ratio = result.width / result.height
    if abs(result_ratio / original_ratio - 1) > _MAX_ASPECT_RATIO_DRIFT:
        raise ValueError("aspect_ratio_changed")

    # Compare at the source resolution. A wide structural change or new crop makes
    # the edit fail closed; the untouched source remains available in the database.
    comparable = ImageOps.contain(result, original.size, Image.Resampling.LANCZOS)
    canvas_color = tuple(round(channel) for channel in ImageStat.Stat(original).mean[:3])
    canvas = Image.new("RGB", original.size, canvas_color)
    canvas.paste(comparable, ((original.width - comparable.width) // 2,
                              (original.height - comparable.height) // 2))
    rms = ImageStat.Stat(ImageChops.difference(original, canvas)).rms
    if sum(rms) / len(rms) > _MAX_DOWNSCALED_RMS:
        raise ValueError("source_art_changed_too_much")
    if result.width * result.height > 40_000_000:
        raise ValueError("upscaled_image_pixel_limit_exceeded")
    if len(result_bytes) > _MAX_OUTPUT_BYTES:
        raise ValueError("upscaled_image_file_limit_exceeded")
    return result.size


def _usage_record(response, model: str, elapsed: float) -> UsageRecord:
    usage = getattr(response, "usage", None)
    details = usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else {}
    input_tokens = int(details.get("input_tokens", 0) or 0)
    output_tokens = int(details.get("output_tokens", 0) or 0)
    input_details = details.get("input_tokens_details") or {}
    output_details = details.get("output_tokens_details") or {}
    image_input = int(input_details.get("image_tokens", 0) or 0)
    text_input = int(input_details.get("text_tokens", 0) or 0)
    image_output = int(output_details.get("image_tokens", 0) or 0)
    text_output = int(output_details.get("text_tokens", 0) or 0)
    if image_input + text_input == 0:
        image_input = input_tokens
    if image_output + text_output == 0:
        image_output = output_tokens
    estimated_cost = (
        image_input * 8.0 + text_input * 5.0 + image_output * 30.0 + text_output * 30.0
    ) / 1_000_000
    return UsageRecord(
        model=model,
        stage="source_image_upscale",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(details.get("total_tokens", input_tokens + output_tokens) or 0),
        estimated_cost_usd=estimated_cost,
        elapsed_seconds=elapsed,
    )


def upscale_source_image(
    image: ImageAsset,
    *,
    get_image_bytes,
    api_key: str,
    model: str,
) -> tuple[ImageAsset, UsageRecord]:
    """Create a provenance-linked derivative; reject edits that alter the source."""
    source_bytes = _source_bytes(image, get_image_bytes)
    if not source_bytes:
        raise ValueError("source_image_bytes_unavailable")

    with Image.open(io.BytesIO(source_bytes)) as original_file:
        source_format = (original_file.format or "PNG").lower()
    source_mime = image.media_type or {
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(source_format, "image/png")
    extension = "jpg" if source_mime == "image/jpeg" else source_format

    started = perf_counter()
    response = OpenAI(api_key=api_key).images.edit(
        model=model,
        image=(f"{image.id}.{extension}", source_bytes, source_mime),
        prompt=_PROMPT,
        size="auto",
        quality="high",
        output_format="png",
    )
    elapsed = perf_counter() - started
    if not response.data or not response.data[0].b64_json:
        raise ValueError("upscale_api_returned_no_image")
    result_bytes = base64.b64decode(response.data[0].b64_json)
    usage = _usage_record(response, model, elapsed)
    try:
        width, height = _verify_result(source_bytes, result_bytes)
    except ValueError as exc:
        raise UpscaleRejected(str(exc), usage) from exc
    derived = image.model_copy(
        update={
            "id": f"{image.id}-upscaled",
            "storage_key": None,
            "media_type": "image/png",
            "file_size_bytes": len(result_bytes),
            "content_bytes": result_bytes,
            "width": width,
            "height": height,
            "fingerprint": None,
            "derived_from_image_id": image.id,
            "enhancement_model": model,
            "source_description": (
                f"{image.source_description} | AI super-resolution from "
                f"{image.width}x{image.height}; source artwork preserved"
            ).strip(" |"),
            "download_status": "downloaded",
            "download_error": None,
        }
    )
    return derived, usage
