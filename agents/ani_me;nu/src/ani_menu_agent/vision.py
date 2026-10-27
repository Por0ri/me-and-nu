"""LLM 없이 투명 이미지의 실제 피사체 영역을 계산합니다."""

from pathlib import Path

from PIL import Image

from ani_menu_agent.models import NormalizedBox


def detect_alpha_subject_box(path: Path, alpha_threshold: int = 8) -> NormalizedBox | None:
    """알파 채널의 불투명 픽셀 경계를 0~1 좌표로 반환합니다."""
    with Image.open(path).convert("RGBA") as image:
        alpha = image.getchannel("A")
        mask = alpha.point(lambda value: 255 if value > alpha_threshold else 0)
        bbox = mask.getbbox()
        if bbox is None:
            return None
        left, top, right, bottom = bbox
        width, height = image.size
        return NormalizedBox(
            x=left / width,
            y=top / height,
            width=(right - left) / width,
            height=(bottom - top) / height,
        )
