"""편집·이미지·레이아웃 가드레일의 단일 진실 공급원."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorialPolicy:
    brand_name: str = "me;nu"
    min_cards: int = 3
    max_cards: int = 10
    default_event_retention_days: int = 30
    default_content_validity_days: int = 90
    max_review_failures: int = 3
    min_official_sources: int = 1
    max_title_chars: int = 42
    max_body_chars: int = 120
    max_ending_chars: int = 54
    max_cover_vertical_gap: float = 0.13
    logical_card_width: int = 327
    logical_card_height: int = 308
    image_quality_target_dpr: float = 4.0
    max_image_upscale: float = 1.25
    minimum_distinct_images_before_generation: int = 3
    # Explicit product-risk decision from the owner: public official images may be used
    # editorially after provenance checks. This setting does not grant legal permission.
    allow_public_official_image_risk: bool = True


POLICY = EditorialPolicy()

# 작품명만 바꾸면 재사용 가능한 상투 문구는 기본 차단한다.
GENERIC_HUMOR_TERMS = (
    "통장이 열린다",
    "지갑이 열린다",
    "덕후라면",
    "과몰입",
    "작전명:",
    "결론:",
    "공식 사이트에서 확인",
    "공식 사이트에서 일정을 확인",
    "배포 정보를 확인할 차례",
    "자세한 내용은 공식",
    "계속 지켜보자",
    "확인해 보세요",
)

AGENT_MONOLOGUE_TERMS = (
    "추측성 내용은 적지",
    "토론은 필요",
    "에이전트",
    "검증을 완료",
    "공식 정보만 사용",
)

EXCESSIVE_PUNCTUATION = ("ㅋㅋ", "ㅎㅎ", "!!!", "???", "?!?", "!!")
