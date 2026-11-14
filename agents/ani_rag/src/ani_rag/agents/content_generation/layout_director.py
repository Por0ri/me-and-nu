"""Image-aware per-page art direction, ported from me;nu's layout director."""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image, ImageOps

from ani_rag.agents.content_generation.prompts import LAYOUT_POLICY
from ani_rag.content import CardDraft, CardLayout, ImageAsset, NormalizedBox

logger = logging.getLogger(__name__)


class ImageCopyMismatchError(ValueError):
    """The selected source image cannot be visually matched to its page copy."""

    def __init__(
        self, message: str, *, card_id: str | None = None,
        image_asset_id: str | None = None, headline: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.card_id = card_id
        self.image_asset_id = image_asset_id
        self.headline = headline
        self.body = body


LAYOUT_SCHEMA: dict[str, object] = {
    "type": "object", "additionalProperties": False,
    "properties": {"cards": {"type": "array", "minItems": 3, "maxItems": 10,
        "items": {"type": "object", "additionalProperties": False,
            "properties": {
                "card_id": {"type": "string"},
                "copy_position": {"type": "string", "enum": ["bottom"]},
                "image_matches_copy": {"type": "boolean"},
                "image_match_reason": {"type": "string", "maxLength": 180},
                "image_quality_acceptable": {"type": "boolean"},
                "image_quality_reason": {"type": "string", "maxLength": 180},
                "image_emphasis": {"type": "string", "enum": ["dominant", "balanced"]},
                "background_style": {"type": "string", "enum": ["blur", "palette", "gradient"]},
            },
            "required": ["card_id", "copy_position", "image_matches_copy", "image_match_reason",
                         "image_quality_acceptable", "image_quality_reason",
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


def _orientation_image_box(asset: ImageAsset) -> NormalizedBox:
    """Choose a larger art field for very wide images while reserving lower copy space."""
    width, height = asset.width, asset.height
    if not width or not height:
        if not asset.local_path:
            return NormalizedBox(0.02, 0.02, 0.96, 0.96)
        with Image.open(asset.local_path) as opened:
            width, height = opened.size
    ratio = width / height
    if ratio < 0.82:
        # A portrait image nearly matches the full card's 327:408 aspect ratio.
        # It sits behind the lower HTML copy, over the existing copy scrim.
        return NormalizedBox(0.01, 0.01, 0.98, 0.98)
    if ratio < 1.25:
        return NormalizedBox(0.02, 0.02, 0.96, 0.90)
    if ratio >= 2.4:
        # Dominant layouts may crop this deeper panorama frame; balanced layouts
        # preserve the complete image inside it.
        return NormalizedBox(0.01, 0.04, 0.98, 0.48)
    # Landscape art remains uncropped and uses as much horizontal room as possible.
    return NormalizedBox(0.01, 0.03, 0.98, 0.70)


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
        try:
            preview = _image_preview(asset) if asset else None
        except (OSError, ValueError) as error:
            preview = None
        if preview is None:
            raise ImageCopyMismatchError(
                f"Card {card.card_id!r} has no readable registered image",
                card_id=card.card_id, image_asset_id=card.image_asset_id,
                headline=card.headline, body=card.body,
            )
        if asset.work_link_status not in {"matched", "candidate"} or not asset.menu_work_ids:
            raise ImageCopyMismatchError(f"Card {card.card_id!r} image has no local work candidate")
        image_urls.append(preview)
        page_rows.append({
            "page": page, "card_id": card.card_id, "role": card.role,
            "headline": card.headline, "body": card.body,
            "image_width": asset.width, "image_height": asset.height,
            "image_aspect_ratio": round(asset.width / asset.height, 3)
            if asset.width and asset.height else None,
            "image_source_id": asset.source_id,
            "image_source_page_url": asset.source_page_url,
            "image_role": asset.image_role,
            "image_alt_text": asset.alt_text,
            "image_source_native_id": asset.source_native_id,
            "image_work_link_status": asset.work_link_status,
            "candidate_menu_work_ids": list(asset.menu_work_ids),
            "image_entity_links": [{
                "entity_id": link.entity_id, "entity_name": link.entity_name,
                "relation_type": link.relation_type,
            } for link in asset.entity_links],
        })
    raw = await vision_call(
        system_prompt=LAYOUT_POLICY,
        user_payload={
            "task": "Choose image framing and emphasis that fills the art area without a visible pasted-on tile.",
            "image_check": (
                "Treat a readable image from the confirmed work or a related character as a valid broad match, "
                "even when it shows a different scene, action, age, episode, season, or story moment. Prefer the "
                "exact named character when convenient, but do not require it. Set image_matches_copy false only "
                "when the work/person is clearly wrong or the image is unusable; do not call a same-work image weak "
                "just because it does not illustrate the exact sentence. Use dominant emphasis for attractive "
                "same-work art. Use balanced emphasis only when the composition itself needs breathing room. "
                "For very wide panoramas, choose dominant only if a centered crop keeps the main subjects; otherwise "
                "choose balanced and preserve the full image. Judge image_quality_acceptable separately from "
                "image_matches_copy: mark quality false only when visibly pixelated/soft at card size and a sharper "
                "candidate should be sought. Prefer sharp, full-size artwork over a small thumbnail. "
                "Never claim that an image depicts a fact it does not show."
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
        if row.get("image_quality_acceptable") is not True:
            reason = str(row.get("image_quality_reason") or "image is visibly pixelated at card size")[:180]
            raise ImageCopyMismatchError(
                f"Selected image needs a sharper source: {reason}",
                card_id=card.card_id, image_asset_id=card.image_asset_id,
                headline=card.headline, body=card.body,
            )
        image_match_is_strong = row.get("image_matches_copy") is True
        if not image_match_is_strong:
            reason = str(row.get("image_match_reason") or "uncertain image-to-copy match")[:180]
            logger.warning(
                "image_copy_match_low card_id=%s asset_id=%s reason=%s",
                card.card_id, card.image_asset_id, reason,
            )
        emphasis = str(row.get("image_emphasis", "dominant"))
        if emphasis not in {"dominant", "balanced"}:
            raise ValueError("Vision layout returned an unsupported image emphasis")
        background_style = str(row.get("background_style", "blur"))
        if background_style not in {"blur", "palette", "gradient"}:
            raise ValueError("Vision layout returned an unsupported background style")
        image_box = _orientation_image_box(assets[card.image_asset_id])
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
