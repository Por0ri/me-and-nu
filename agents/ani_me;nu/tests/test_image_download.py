import io

import pytest
from PIL import Image, ImageDraw

from ani_menu_agent.agents.image.download import _validated_image


def test_image_validation_extracts_real_transparency_and_subject_bounds() -> None:
    source = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    ImageDraw.Draw(source).ellipse((40, 30, 160, 270), fill=(40, 120, 240, 255))
    payload = io.BytesIO()
    source.save(payload, format="PNG")

    suffix, width, height, has_alpha, subject_box = _validated_image(
        payload.getvalue(), "image/png"
    )

    assert (suffix, width, height) == (".png", 200, 300)
    assert has_alpha is True
    assert subject_box is not None
    assert subject_box.x == 0.2
    assert subject_box.y == 0.1
    assert subject_box.width == pytest.approx(0.605)
    assert subject_box.height == pytest.approx(0.803333, abs=1e-5)


def test_image_validation_does_not_mark_opaque_image_as_cutout() -> None:
    source = Image.new("RGB", (80, 60), "white")
    payload = io.BytesIO()
    source.save(payload, format="JPEG")

    _, _, _, has_alpha, subject_box = _validated_image(payload.getvalue(), "image/jpeg")

    assert has_alpha is False
    assert subject_box is None
