"""Guarded, background-only image edit for cards rejected on composition."""

from __future__ import annotations

import base64
import io
from time import perf_counter

from openai import OpenAI
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ani_menu_agent.agents.content_generation.renderer import OUTPUT_IMAGE_SIZE
from ani_menu_agent.models import NormalizedBox, UsageRecord

_PROTECT_MARGIN = 28
_BLEND_RADIUS = 14
_PROMPT = """Make a subtle editorial background treatment for this anime carousel card.
Only improve the empty outer background areas selected by the transparent mask. Continue
the existing image's color palette and lighting so the background feels connected to the
original artwork. Do not add characters, objects, logos, symbols, lettering, captions, or
new story details. Do not alter, redraw, extend, reinterpret, or replace any source artwork.
The central artwork and HTML text-safe area are protected and must remain visually unchanged.
Keep the output clean and restrained, with enough contrast for later HTML text placement."""


def _pixel_box(box: NormalizedBox, width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, min(width, round(box.x * width)))
    top = max(0, min(height, round(box.y * height)))
    right = max(left, min(width, round((box.x + box.width) * width)))
    bottom = max(top, min(height, round((box.y + box.height) * height)))
    return left, top, right, bottom


def _masks(
    size: tuple[int, int], protected_boxes: list[NormalizedBox]
) -> tuple[bytes, Image.Image, list[tuple[int, int, int, int]]]:
    width, height = size
    api_mask = Image.new("RGBA", size, (0, 0, 0, 0))
    api_draw = ImageDraw.Draw(api_mask)
    exact_boxes: list[tuple[int, int, int, int]] = []
    for box in protected_boxes:
        left, top, right, bottom = _pixel_box(box, width, height)
        exact_boxes.append((left, top, right, bottom))
        api_draw.rectangle(
            (
                max(0, left - _PROTECT_MARGIN),
                max(0, top - _PROTECT_MARGIN),
                min(width - 1, right + _PROTECT_MARGIN),
                min(height - 1, bottom + _PROTECT_MARGIN),
            ),
            fill=(255, 255, 255, 255),
        )
    encoded_mask = io.BytesIO()
    api_mask.save(encoded_mask, format="PNG")

    editable = Image.new("L", size, 255)
    editable_draw = ImageDraw.Draw(editable)
    for left, top, right, bottom in exact_boxes:
        editable_draw.rectangle((left, top, right - 1, bottom - 1), fill=0)
    return encoded_mask.getvalue(), editable.filter(ImageFilter.GaussianBlur(_BLEND_RADIUS)), exact_boxes


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
        image_input * 8.0
        + text_input * 5.0
        + image_output * 30.0
        + text_output * 30.0
    ) / 1_000_000
    return UsageRecord(
        model=model,
        stage="guarded_background_image_edit",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(details.get("total_tokens", input_tokens + output_tokens) or 0),
        estimated_cost_usd=estimated_cost,
        elapsed_seconds=elapsed,
    )


def edit_background_only(
    base_png: bytes,
    *,
    protected_boxes: list[NormalizedBox],
    api_key: str,
    model: str,
) -> tuple[bytes, UsageRecord]:
    """Edit only the masked background and restore protected pixels from the base PNG."""
    with Image.open(io.BytesIO(base_png)) as opened:
        base = opened.convert("RGBA")
    if base.size != OUTPUT_IMAGE_SIZE:
        raise ValueError(f"Unexpected card image size: {base.size}")

    mask_bytes, blend_alpha, exact_boxes = _masks(base.size, protected_boxes)
    started = perf_counter()
    response = OpenAI(api_key=api_key).images.edit(
        model=model,
        image=("card.png", base_png, "image/png"),
        mask=("protected_regions.png", mask_bytes, "image/png"),
        prompt=_PROMPT,
        size="auto",
        quality="low",
        output_format="png",
    )
    elapsed = perf_counter() - started
    if not response.data or not response.data[0].b64_json:
        raise ValueError("Image edit API returned no image data")
    edited_bytes = base64.b64decode(response.data[0].b64_json)
    with Image.open(io.BytesIO(edited_bytes)) as opened:
        edited = opened.convert("RGBA")
    if edited.size != base.size:
        edited = edited.resize(base.size, Image.Resampling.LANCZOS)

    # The API mask is guidance, not a strict pixel boundary. Blend its result only
    # outside the protected regions, then restore every protected pixel exactly.
    result = Image.composite(edited, base, blend_alpha)
    for left, top, right, bottom in exact_boxes:
        if right > left and bottom > top:
            result.paste(base.crop((left, top, right, bottom)), (left, top))
    if any(
        ImageChops.difference(
            base.crop((left, top, right, bottom)),
            result.crop((left, top, right, bottom)),
        ).convert("RGB").getbbox()
        for left, top, right, bottom in exact_boxes
        if right > left and bottom > top
    ):
        raise ValueError("Protected card pixels changed; background edit rejected")

    output = io.BytesIO()
    result.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue(), _usage_record(response, model, elapsed)
