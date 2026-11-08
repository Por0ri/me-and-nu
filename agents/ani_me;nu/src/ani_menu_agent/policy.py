"""편집·이미지·레이아웃 가드레일의 단일 진실 공급원."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorialPolicy:
    brand_name: str = "me;nu"
    # Keep generation LLM spend below four cents per package. Reserve one cent
    # before starting another revision because that revision needs writing and review.
    max_content_llm_cost_usd: float = 0.04
    revision_cost_reserve_usd: float = 0.01
    # Recent image-upscale calls cost more than the full package budget.
    image_model_cost_budget_usd: float = 0.0
    # Every carousel must have a cover, at least one detail card, and a payoff.
    min_cards: int = 3
    # Timely stories often carry only a couple of usable visuals.
    min_short_cards: int = 2
    max_cards: int = 10
    max_extraction_fact_candidates: int = 100
    max_verified_facts: int = 30
    max_candidate_retries: int = 1
    default_event_retention_days: int = 30
    default_content_validity_days: int = 90
    max_review_failures: int = 4
    max_title_chars: int = 42
    max_body_chars: int = 60
    # End cards may carry a compact fact recap plus a punchline; 54 was
    # repeatedly rejecting readable copy before the independent language review.
    max_ending_chars: int = 90
    max_cover_vertical_gap: float = 0.13
    logical_card_width: int = 327
    logical_card_height: int = 408
    image_quality_target_dpr: float = 4.0
    max_image_upscale: float = 1.25
    # max_image_upscale is the clean target, and AI upscaling starts above it. A card
    # may still stretch further: the target assumes a 4x screen, so this limit is about
    # 1.6x on a common 2x phone. Stricter limits rejected ordinary 640px product photos
    # and 400px episode stills outright, which left whole decks without images.
    max_card_image_upscale: float = 3.2
    # A cover takes a source within this sharper limit first, whenever one exists.
    preferred_cover_image_upscale: float = 2.5
    # Small official portraits can occupy a meaningful area in a contain spotlight;
    # 2x is the compromise between usable visual scale and preserving sharpness.
    # Transparent cutouts and other layouts keep the stricter general limit above.
    max_low_res_spotlight_upscale: float = 2.0
    quality_pass_score: int = 68
    quality_card_minimum_score: int = 55
    safety_minimum_per_card: int = 4
    default_review_issue_penalty: int = 12
    review_issue_penalties: tuple[tuple[str, int], ...] = (
        ("EDGE_ATTACHED_IMAGE", 5),
        ("COVER_EMPTY_GAP", 4),
        ("FOREIGN_IMAGE_UNEXPLAINED", 8),
        ("TITLE_TOO_LONG", 6),
        ("BODY_TOO_LONG", 6),
        ("AGENT_MONOLOGUE", 5),
        ("RESEARCH_DELEGATED_TO_USER", 7),
        ("PROTECTED_PHRASE_LOST", 8),
        ("PROTECTED_PHRASE_SPLIT", 8),
        ("CARD_REDUNDANT", 6),
        ("ENDING_TOO_LONG", 4),
        ("ENDING_GENERIC", 6),
        ("ENDING_LOUD", 3),
        ("IMAGE_LOW_RESOLUTION", 16),
        ("IMAGE_REUSED_RAW", 16),
        ("JAPANESE_COPY_UNTRANSLATED", 16),
        ("JAPANESE_TERM_MISTRANSLATED", 18),
        ("JAPANESE_TERM_UNPAIRED", 16),
    )
    image_relevance_minimum: int = 2
    image_composition_minimum: int = 2
    content_type_fit_minimum: int = 2
    detail_factual_support_minimum: int = 3
    detail_completeness_minimum: int = 3
    language_korean_minimum: int = 2
    language_translation_minimum: int = 2
    language_clarity_minimum: int = 2
    language_fan_engagement_minimum: int = 2
    language_card_flow_minimum: int = 2
    # Explicit product-risk decision from the owner: public official images may be used
    # editorially after provenance checks. This setting does not grant legal permission.
    allow_public_official_image_risk: bool = True
    # Additionally accepts attributed public imagery embedded in the exact
    # registered same-work editorial page. This is not a license or permission.
    allow_public_editorial_image_risk: bool = True


POLICY = EditorialPolicy()

REAL_WORLD_TYPES = frozenset({"event", "goods"})
SHORT_DECK_TYPES = REAL_WORLD_TYPES | {"news", "review"}


def min_cards_for(content_type: str | None) -> int:
    return POLICY.min_short_cards if content_type in SHORT_DECK_TYPES else POLICY.min_cards


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
    "작품 연결은",
    "근거가 엇갈",
    "확인된 자료에",
)

EXCESSIVE_PUNCTUATION = ("ㅋㅋ", "ㅎㅎ", "!!!", "???", "?!?", "!!")
