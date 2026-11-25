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
    # 사전 글의 장수는 글감이 정한다 (target_cards). 생일 글은 2장까지 내려간다 (서버도 2장부터 받음)
    min_cards: int = 2
    max_cards: int = 10
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
    # 사전 글은 포스터 위주라 표지와 같은 2.5배까지 허용
    max_image_upscale: float = 2.5
    # Small official portraits can occupy a meaningful area in a contain spotlight;
    # 2x is the compromise between usable visual scale and preserving sharpness.
    # Transparent cutouts and other layouts keep the stricter general limit above.
    # 작은 캐릭터 사진은 3.5배까지 키워서 카드에 크게 넣는다 (렌더러가 선명하게 보정)
    max_low_res_spotlight_upscale: float = 3.5
    # 상세 글의 바깥 한계 (공백 포함). 유형별 길이는 아래 DETAIL_LENGTH_BY_KIND (11/11)
    min_detail_chars: int = 100
    max_detail_chars: int = 3000
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
    # 상세 글 자연스러움 · 흐름은 3점부터 통과 (11/11: 2점 본문이 고치지 않고 통과한 구멍을 막음)
    detail_language_minimum: int = 3
    # 상세 글 덕후 말맛도 3점부터 (반말로 사실만 요약한 글은 2점이라 다시 쓴다)
    detail_fan_voice_minimum: int = 3
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

# 상세 글 길이 (공백 뺀 글자 수, 11/11 서원). 키는 글 번호의 세 번째 조각 (dict-<묶음>-<유형>-…)
# 11/11 밤 서원: 처음 정한 길이(350~550 등)로 쓰니 폰에서 28줄이라 너무 길어서 3분의 2 정도로 줄임
DETAIL_LENGTH_BY_KIND: dict[str, tuple[int, int]] = {
    **dict.fromkeys(("like", "overlap", "va", "krva", "studio", "director", "artist", "themes", "award",
                     "boxoffice"), (250, 380)),
    **dict.fromkeys(("intro", "basic", "origin", "author", "place"), (220, 320)),
    **dict.fromkeys(("profile", "bdaybook", "bdaymonth"), (180, 260)),
}
# 프로필 · 생일 글에 캐릭터 참고 글(나무위키 성격 · 특징 문단)이 붙으면 더 쓸 거리가 있다
DETAIL_LENGTH_WITH_CHARACTER_REF = (220, 300)
DETAIL_LENGTH_DEFAULT = (220, 320)


def request_kind(request_id: str) -> str:
    """사전 글의 유형 (글 번호의 세 번째 조각, dict-<묶음>-<유형>-…). 사전 글이 아니면 빈 문자열"""
    parts = (request_id or "").split("-")
    return parts[2] if len(parts) > 2 and parts[0] == "dict" else ""


# 11/12 서원: 표지는 머릿글 · 큰 제목 · 작은 설명을 다 채운다 (서버 글 64개가 AI 가 칸을 비워 들쭉날쭉했음).
# 입문 가이드 · 원작 이야기 표지 머릿글은 AI 가 아니라 코드가 고정해서 넣는다.
COVER_EYEBROW_BY_KIND = {"basic": "입문 가이드", "origin": "원작 이야기"}
# 표지 작은 설명을 비워도 되는 유형 (11/11 서원 결정: 프로필 표지는 큰 글씨만, 이달의 생일은 비우거나 짧은 축하)
COVER_BODY_OPTIONAL_KINDS = frozenset({"profile", "bdaymonth"})


def detail_length_range(request_id: str, has_character_ref: bool = False) -> tuple[int, int]:
    """(최소, 최대) 공백 뺀 글자 수"""
    kind = request_kind(request_id)
    if has_character_ref and kind in ("profile", "bdaybook", "bdaymonth"):
        return DETAIL_LENGTH_WITH_CHARACTER_REF
    return DETAIL_LENGTH_BY_KIND.get(kind, DETAIL_LENGTH_DEFAULT)


# 상세 글에 쓰지 않는 상투 문구 (11/11 서원 본문 가이드)
DETAIL_STOCK_PATTERNS = (
    r"따라가다\s?보면", r"하는\s?맛", r"다시\s?보고\s?싶어", r"기억해\s?(?:두|둬|둘)", r"한눈에", r"정리하면",
    r"살펴보자", r"알아보자",
    # 11/11 시범 18개에서 글마다 되풀이된 맺음말
    r"또렷해", r"선명해", r"감상\s?포인트", r"떠올려\s?(?:봐|보자|보면)", r"출발(?:점|선)(?:부터|이|은)?\s?(?:확실히\s?|매번\s?)?다르",
    r"한\s?장르에\s?머물지", r"선명하", r"떠올려\s?보게", r"이렇게\s?(?:다르|달라)",
    # 흔한 도입 · 흐린 끝맺음, 예시 문구 베끼기 (11/11 밤: 예시의 펀치라인을 그대로 쓰지 않게)
    r"고민이라면", r"궁금하다면", r"출발선", r"갈래야", r"스케일\s?사기", r"잡화점", r"장르\s?환승", r"진짜는\s?그다음",
)

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

# 독자가 보는 글에 나오면 안 되는 자료 설명 말투 (11/10 서원: "분석한 사실만 적어 놓은 느낌")
# 출처 이름 · 내부 점수 · 기록 말투. "흥행 기록" 처럼 자연스러운 말은 걸리지 않게 좁게 잡는다.
DATA_TALK_PATTERNS = (
    r"AniList", r"애니리스트", r"위키데이터", r"Wikidata", r"MyAnimeList", r"마이애니메리스트", r"나무\s?위키",
    r"정보\s?사전", r"추천\s?점수", r"추천\s?기록", r"사용자\s?추천", r"(?<!해시)태그",
    r"기록(?:돼|되어)\s?있", r"로\s?기록(?:돼|되어|된|됐)", r"기록\s?기준", r"확인된",
    r"프로필에\s?적힌", r"작품\s?기준",
)
