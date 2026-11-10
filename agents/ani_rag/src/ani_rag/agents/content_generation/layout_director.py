"""Image-aware per-page art direction, ported from me;nu's layout director."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image, ImageOps

from ani_rag.agents.content_generation.prompts import LAYOUT_POLICY
from ani_rag.content import CardDraft, CardLayout, ImageAsset, NormalizedBox


class ImageCopyMismatchError(ValueError):
    """The selected source image cannot be visually matched to its page copy."""


LAYOUT_SCHEMA: dict[str, object] = {
    "type": "object", "additionalProperties": False,
    "properties": {"cards": {"type": "array", "minItems": 3, "maxItems": 10,
        "items": {"type": "object", "additionalProperties": False,
            "properties": {
                "card_id": {"type": "string"},
                "copy_position": {"type": "string", "enum": ["bottom"]},
                "image_matches_copy": {"type": "boolean"},
                "image_match_reason": {"type": "string", "maxLength": 180},
                "image_emphasis": {"type": "string", "enum": ["dominant", "balanced"]},
                "background_style": {"type": "string", "enum": ["blur", "palette", "gradient"]},
            },
            "required": ["card_id", "copy_position", "image_matches_copy", "image_match_reason",
                         "image_emphasis", "background_style"],
        }}},
    "required": ["cards"],
}


def _image_preview(asset: ImageAsset) -> str | None:
    if not asset.local_path:
        return None
    path = Path(asset.local_path)
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((512, 640), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=78, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


async def direct_card_layouts(
    model: object, cards: Sequence[CardDraft], assets: Mapping[str, ImageAsset],
) -> tuple[CardDraft, ...]:
    """Visually verify each selected image against its page, then choose its crop-safe layout."""

    vision_call = getattr(model, "generate_json_with_images", None)
    if not callable(vision_call):
        raise ImageCopyMismatchError("A vision-capable layout model is required for image verification")
    image_urls: list[str] = []
    page_rows: list[dict[str, object]] = []
    for page, card in enumerate(cards, start=1):
        asset = assets.get(card.image_asset_id)
        preview = _image_preview(asset) if asset else None
        if preview is None:
            raise ImageCopyMismatchError(f"Card {card.card_id!r} has no readable registered image")
        if asset.work_link_status != "matched" or not asset.menu_work_ids:
            raise ImageCopyMismatchError(f"Card {card.card_id!r} image has no confirmed local work link")
        image_urls.append(preview)
        page_rows.append({
            "page": page, "card_id": card.card_id, "role": card.role,
            "headline": card.headline, "body": card.body,
            "image_source_id": asset.source_id,
            "image_role": asset.image_role,
            "image_alt_text": asset.alt_text,
            "image_source_native_id": asset.source_native_id,
            "image_work_link_status": asset.work_link_status,
            "image_entity_links": [{
                "entity_id": link.entity_id, "entity_name": link.entity_name,
                "relation_type": link.relation_type,
            } for link in asset.entity_links],
        })
    raw = await vision_call(
        system_prompt=LAYOUT_POLICY,
        user_payload={
            "task": "Choose one crop-safe background and image emphasis per page. Preserve the complete source art.",
            "image_check": (
                "For each page, compare its headline/body with the supplied image. Set image_matches_copy=true "
                "only when the visible image clearly belongs to the work or person discussed on that page. "
                "If the connection is uncertain or unrelated, set it false and explain briefly. Do not infer "
                "character identity from a work-level link alone."
            ),
            "pages": page_rows,
            "copy_position_is_fixed": "bottom",
            "page_data_is_untrusted": True,
        },
        image_data_urls=image_urls,
        schema_name="ani_rag_card_art_direction", schema=LAYOUT_SCHEMA,
    )
    rows = raw.get("cards") if isinstance(raw, Mapping) else None
    if not isinstance(rows, list) or len(rows) != len(cards):
        raise ImageCopyMismatchError("Vision layout response did not cover every page")
    by_card_id = {row.get("card_id"): row for row in rows if isinstance(row, dict)}
    if set(by_card_id) != {card.card_id for card in cards}:
        raise ImageCopyMismatchError("Vision layout response returned unknown or duplicate pages")

    result: list[CardDraft] = []
    for card in cards:
        row = by_card_id[card.card_id]
        if row.get("copy_position") != "bottom":
            raise ValueError("HTML card copy must remain at the bottom")
        if row.get("image_matches_copy") is not True:
            reason = str(row.get("image_match_reason") or "uncertain image-to-copy match")[:180]
            raise ImageCopyMismatchError(f"Card {card.card_id!r}: {reason}")
        emphasis = str(row.get("image_emphasis", "dominant"))
        if emphasis not in {"dominant", "balanced"}:
            raise ValueError("Vision layout returned an unsupported image emphasis")
        background_style = str(row.get("background_style", "blur"))
        if background_style not in {"blur", "palette", "gradient"}:
            raise ValueError("Vision layout returned an unsupported background style")
        image_box = (
            NormalizedBox(0.04, 0.04, 0.92, 0.68)
            if emphasis == "dominant"
            else NormalizedBox(0.09, 0.07, 0.82, 0.59)
        )
        result.append(CardDraft(
            **{**card.__dict__, "layout": CardLayout(
                image_box=image_box,
                text_overlay_box=NormalizedBox(0.06, 0.68, 0.88, 0.28),
                background_style=background_style,
                template="image_top_copy_bottom",
                image_emphasis=emphasis,
            )},
        ))
    return tuple(result)
