"""LLM을 쓰지 않고도 매번 동일하게 적용되는 품질 검사."""

import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from ani_rag.content.image_selection import (
    image_belongs_to_package_sources,
    image_visual_key,
    image_visual_keys,
    is_editorially_composable,
)
from ani_rag.content.models import ContentKind, ContentPackage, QualityIssue
from ani_rag.content.policy import (
    AGENT_MONOLOGUE_TERMS,
    COVER_BODY_OPTIONAL_KINDS,
    DATA_TALK_PATTERNS,
    DETAIL_STOCK_PATTERNS,
    EXCESSIVE_PUNCTUATION,
    GENERIC_HUMOR_TERMS,
    POLICY,
    detail_length_range,
    request_kind,
)
from ani_rag.content.review_rules import RULES, decision_metadata, make_issue, score_penalty

_DATA_TALK = re.compile("|".join(DATA_TALK_PATTERNS))
_DETAIL_STOCK = re.compile("|".join(DETAIL_STOCK_PATTERNS))
# 11/11: 1차 본문 41개에 섞여 있던 것들 (보이지 않는 글자, 아랍 · 러시아 · 칸나다 문자, "[100자 이상]",
# "Actually edit typo", "[f1-f4]", "】【。", "**굵게**")
INVISIBLE_CHARS = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")
_FOREIGN_SCRIPT = re.compile(r"[Ѐ-ԯ԰-ࣿऀ-෿฀-๿Ⴀ-ჿ]")
_DETAIL_LEAK = re.compile(
    r"[가-힣]。|\*\*|fact_ids?|(?<![A-Za-z0-9])f\d{1,3}(?![A-Za-z0-9])"
    r"|\[[^\]]{0,40}(?:\d+\s*자|사실|카드|근거|문장|아티클|본문)[^\]]{0,40}\]"
)
# 【】 는 제목에 원래 들어가기도 해서 (【최애의 아이】) 사실 · 카드에 없는 것만 오염으로 본다
_LENTICULAR = re.compile(r"【[^【】]{1,40}】")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]{3,}")
# 11/11 서원: 말투는 세게 가도 욕은 안 된다 ("그냥 욕만 없으면 될 것 같아"). '시발점'은 걸리지 않게
_PROFANITY = re.compile(r"존나|졸라|ㅈㄴ|씨발|씨바|시발(?!점)|ㅅㅂ|개새|병신|ㅂㅅ|좆|지랄|닥쳐|꺼져|엿\s?먹")
_LATIN_OK = {"blu-ray", "mbti"}


def _data_talk(text: str) -> str:
    """독자용 글에 남은 자료 설명 말투를 찾아 쉼표로 이어 돌려준다 (없으면 빈 문자열)"""
    return ", ".join(sorted({match.group(0) for match in _DATA_TALK.finditer(text or "")}))


def _date_in_quote(quote: str, default_year: int | None = None) -> list[str]:
    dates: list[str] = []
    inherited_year: int | None = default_year
    inherited_month: int | None = None
    pattern = re.compile(
        r"(?:(20\d{2})\s*(?:年|년|[./-])\s*)?"
        r"(\d{1,2})\s*(?:月|월|[./-])\s*(\d{1,2})\s*日?"
    )
    for match in pattern.finditer(quote):
        if match.group(1):
            inherited_year = int(match.group(1))
        inherited_month = int(match.group(2))
        if inherited_year is not None:
            try:
                dates.append(date(inherited_year, inherited_month, int(match.group(3))).isoformat())
            except ValueError:
                pass

    # Korean/Japanese notices often abbreviate the last day in a same-month range:
    # “2026년 10월 23~27일” or “10月23日～27日”. Reuse the nearest verified year/month.
    if inherited_year is not None and inherited_month is not None and dates:
        last_explicit_end = max((match.end() for match in pattern.finditer(quote)), default=0)
        tail = quote[last_explicit_end:]
        abbreviated_end = re.search(r"(?:~|～|〜|–|—|-|부터)\s*(\d{1,2})\s*[일日]", tail)
        if abbreviated_end:
            try:
                end_date = date(
                    inherited_year, inherited_month, int(abbreviated_end.group(1))
                ).isoformat()
                if end_date not in dates:
                    dates.append(end_date)
            except ValueError:
                pass
    return dates


def _issue(
    code: str,
    message: str,
    page: int | None = None,
) -> QualityIssue:
    return make_issue(code=code, message=message, page=page)

def _publication_date_variants(value: str | None) -> tuple[date | None, set[str]]:
    if not value:
        return None, set()
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None, set()
    year, month, day = parsed.year, parsed.month, parsed.day
    variants = {
        f"{year}년{month}월{day}일",
        f"{year}년{month:02}월{day:02}일",
        f"{month}월{day}일",
        f"{month:02}월{day:02}일",
        f"{year}.{month}.{day}",
        f"{year}.{month:02}.{day:02}",
        f"{year}-{month:02}-{day:02}",
    }
    return parsed, variants


def _card_cites_source_date_as_scheduled_fact(
    package: ContentPackage,
    card,
    source_id: str,
    publication_date: date,
) -> bool:
    """A valid event/sales date may coincide with the article's publication date."""
    scheduled_fact_types = {
        "event_schedule",
        "event_period",
        "event_date",
        "product_release",
        "release",
        "release_date",
        "goods_release",
        "sales_start",
    }
    facts_by_id = {fact.id: fact for fact in package.facts}
    for fact_id in card.fact_ids:
        fact = facts_by_id.get(fact_id)
        if (
            fact is None
            or not fact.verified
            or source_id not in fact.source_ids
            or fact.fact_type.strip().lower() not in scheduled_fact_types
        ):
            continue
        quote = str(fact.details.get("source_quote") or "")
        if publication_date.isoformat() in _date_in_quote(
            quote, default_year=publication_date.year
        ):
            return True
    return False


def _has_korean_translation_before_japanese_original(text: str, japanese_start: int) -> bool:
    """Only allow Japanese immediately inside a Korean-first parenthetical name."""
    prefix = text[max(0, japanese_start - 60) : japanese_start]
    return re.search(r"[가-힣][^()\n]{0,30}[（(][^()\n]*$", prefix) is not None


def _is_japanese_hashtag_suffix(text: str, start: int) -> bool:
    """Hashtags are atomic official labels; don't treat their Japanese suffix as prose."""
    token_start = max(text.rfind(" ", 0, start), text.rfind("\n", 0, start)) + 1
    token_end = next(
        (index for index in range(start, len(text)) if text[index].isspace()), len(text)
    )
    token = text[token_start:token_end]
    hashtag_start = token.rfind("#")
    return hashtag_start >= 0 and not any(char.isspace() for char in token[hashtag_start:])


def _protected_phrase_is_present(phrase: str, text: str) -> bool:
    """Match exact facts while tolerating natural Korean particles between phrase words."""
    if phrase in text:
        return True
    words = phrase.split()
    if len(words) < 2:
        return False
    particle = r"(?:은|는|이|가|을|를|에|에서|으로|로|도|만|까지)?"
    pattern = "".join(re.escape(word) + particle + r"\s*" for word in words[:-1])
    pattern += re.escape(words[-1])
    return re.search(pattern, text) is not None


def check_sources(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    source_ids = {source.id for source in package.sources}
    used_fact_ids = {fact_id for card in package.cards for fact_id in card.fact_ids}
    event_dates = {
        parsed
        for value in (package.event_start, package.event_end)
        if value
        for parsed, _variants in [_publication_date_variants(value)]
        if parsed
    }
    for source in package.sources:
        if urlparse(str(source.url)).scheme != "https":
            issues.append(
                _issue("SOURCE_NOT_HTTPS", f"HTTPS가 아닌 출처: {source.url}")
            )
        if source.published_at:
            try:
                published = datetime.fromisoformat(source.published_at.replace("Z", "+00:00"))
                published_date = published.date()
            except ValueError:
                try:
                    published_date = date.fromisoformat(source.published_at[:10])
                except ValueError:
                    published_date = None
            normalized_cards = [
                (card, re.sub(r"\s+", "", " ".join([*card.title_lines, *card.body_sentences])))
                for card in package.cards
            ]
            publication_date, date_variants = _publication_date_variants(source.published_at)
            if publication_date and publication_date not in event_dates and date_variants:
                for card, card_text in normalized_cards:
                    if (
                        any(variant in card_text for variant in date_variants)
                        and not _card_cites_source_date_as_scheduled_fact(
                            package, card, source.id, publication_date
                        )
                    ):
                        issues.append(
                            _issue(
                                "SOURCE_PUBLICATION_DATE_SHOWN",
                                "원문 발표일은 카드에 표기하지 마세요. 행사 시작·종료일과 "
                                "겹치지 않는 원문 게시 날짜가 본문에 들어 있습니다.",
                                card.page,
                            )
                        )
            has_current_claim = any(
                fact.time_sensitive and source.id in fact.source_ids for fact in package.facts
            )
            if (
                package.content_kind == ContentKind.INFORMATION
                and has_current_claim
                and published_date
                and published_date < date.today() - timedelta(days=90)
            ):
                issues.append(
                    _issue(
                        "SOURCE_STALE",
                        "시의성 사실의 원문 게시일이 90일을 넘었습니다. "
                        "최신 공지/상태를 재확인하거나 상시 정보로 다시 분류하세요.",

                    )
                )
    if package.content_type == "event" and package.event_end:
        try:
            event_end = date.fromisoformat(package.event_end[:10])
        except ValueError:
            event_end = None
        if event_end and event_end < date.today():
            issues.append(
                _issue(
                    "EVENT_ALREADY_ENDED",
                    "행사 종료일이 지났습니다. 종료된 행사를 진행 예정처럼 생성하지 말고 "
                    "최신 후보를 사용하세요.",

                )
            )
    for fact in package.facts:
        missing = sorted(set(fact.source_ids) - source_ids)
        if missing:
            issues.append(
                _issue(
                    "FACT_SOURCE_MISSING",
                    f"사실 {fact.id}의 출처가 없습니다: {missing}",

                )
            )
        if fact.id in used_fact_ids and fact.fact_type.strip().lower() in {
            "campaign",
            "promotion",
        }:
            quote = str(fact.details.get("source_quote") or "")
            deadline = fact.details.get("deadline")
            deadline_date: date | None = None
            if isinstance(deadline, str):
                try:
                    deadline_date = date.fromisoformat(deadline[:10])
                except ValueError:
                    pass
            if deadline_date is None and any(token in quote for token in ("まで", "까지")):
                parsed_dates = _date_in_quote(quote)
                if parsed_dates:
                    deadline_date = date.fromisoformat(parsed_dates[-1])
            elif deadline_date is None:
                parsed_dates = _date_in_quote(quote)
                if len(parsed_dates) >= 2:
                    deadline_date = date.fromisoformat(parsed_dates[-1])
            if deadline_date and deadline_date < date.today():
                issues.append(
                    _issue(
                        "CAMPAIGN_EXPIRED",
                        "이미 종료된 캠페인/프로모션 사실을 현재형 콘텐츠에 사용했습니다. "
                        "해당 카드를 삭제하거나 명확한 회고형 콘텐츠로 다시 기획하세요.",
                        next(card.page for card in package.cards if fact.id in card.fact_ids),
                    )
                )
        if package.content_kind == ContentKind.INFORMATION and not fact.verified:
            issues.append(
                _issue("FACT_UNVERIFIED", f"미검증 사실: {fact.statement}")
            )
        if fact.fact_type == "campaign" and fact.id in used_fact_ids:
            required = {"participation_steps", "prize", "deadline", "region"}
            missing = sorted(key for key in required if not fact.details.get(key))
            if missing:
                issues.append(
                    _issue(
                        "CAMPAIGN_DETAILS_MISSING",
                        f"응모 정보의 필수 항목이 빠졌습니다: {', '.join(missing)}",

                    )
                )
    return issues


def check_images(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    images = {image.id: image for image in package.images}
    for card in package.cards:
        if card.visual_mode == "editorial_graphic":
            issues.append(
                _issue(
                    (
                        "COVER_SOURCE_IMAGE_MISSING"
                        if card.role == "cover"
                        or card.page == min(item.page for item in package.cards)
                        else "SOURCE_IMAGE_MISSING"
                    ),
                    "표지에는 관련 출처 이미지가 필요합니다.",
                    card.page,
                )
            )
            continue
        if card.image_reuse_mode == "missing_source_image" or card.image_id.startswith(
            "missing-source-image-"
        ):
            issues.append(
                _issue(
                    "SOURCE_IMAGE_MISSING",
                    "A real, relevant anime image is required for every card.",
                    card.page,
                )
            )
            continue
        image = images.get(card.image_id)
        if not image:
            issues.append(
                _issue(
                    "IMAGE_MISSING",
                    f"이미지 {card.image_id}를 찾을 수 없습니다.",
                    card.page,
                )
            )
            continue
        if not image_belongs_to_package_sources(image, package.sources, package):
            issues.append(
                _issue(
                    "IMAGE_SOURCE_PAGE_MISMATCH",
                    "이미지가 콘텐츠 원문 또는 같은 작품의 출처가 확인된 공식 이미지가 아닙니다. "
                    "다른 작품 이미지나 출처가 불명확한 이미지는 사용할 수 없습니다.",
                    card.page,
                )
            )
        if image.width > 0 and image.height > 0:
            target_width = (
                POLICY.logical_card_width
                * card.layout.image_box.width
                * POLICY.image_quality_target_dpr
            )
            target_height = (
                POLICY.logical_card_height
                * card.layout.image_box.height
                * POLICY.image_quality_target_dpr
            )
            upscale = min(target_width / image.width, target_height / image.height)
            max_upscale = (
                POLICY.max_low_res_spotlight_upscale
                if card.layout.template == "low_res_spotlight"
                else POLICY.max_image_upscale
            )
            if upscale > max_upscale:
                issues.append(
                    _issue(
                        "IMAGE_LOW_RESOLUTION",
                        "원본 이미지 해상도가 카드 표시 크기에 부족합니다. "
                        "더 큰 공식 원본을 수집하세요.",
                        card.page,
                    )
                )
        if (
            image.visible_text
            and any(
                language.lower() not in {"ko", "korean", "한국어"}
                for language in image.detected_languages
            )
            and not image.korean_explanation.strip()
        ):
            issues.append(
                _issue(
                    "FOREIGN_IMAGE_UNEXPLAINED",
                    "이미지 속 외국어 핵심 내용을 한국어로 설명해야 합니다.",
                    card.page,
                )
            )
        if image.visual_match == "exact" and not image.related_fact_ids:
            issues.append(
                _issue(
                    "IMAGE_FACT_LINK_MISSING",
                    (
                        "Exact-match image has no linked verified fact; verify the image-to-fact "
                        "relationship."
                    ),
                    card.page,
                )
            )
        elif image.visual_match == "exact" and not set(card.fact_ids).intersection(
            image.related_fact_ids
        ):
            issues.append(
                _issue(
                    "IMAGE_FACT_MISMATCH",
                    "이미지와 카드가 설명하는 사실이 연결되지 않습니다.",
                    card.page,
                )
            )
        if image.download_status == "failed":
            issues.append(
                _issue(
                    "IMAGE_DOWNLOAD_FAILED",
                    image.download_error or "이미지 파일을 내려받지 못했습니다.",
                    card.page,
                )
            )
        if not image.eligible_for_editorial_use(
            [*package.sources, *([image.provenance_source] if image.provenance_source else [])],
            allow_risk_accepted=(
                POLICY.allow_public_official_image_risk or POLICY.allow_public_editorial_image_risk
            ),
        ):
            issues.append(
                _issue(
                    "IMAGE_RIGHTS_REVIEW",
                    "승인된 공개 이미지 출처와 귀속 정보가 확인되지 않았습니다.",
                    card.page,
                )
            )
        key = image_visual_key(image)
        if (
            image.used_in_previous_content
            or key in package.prior_image_fingerprints
            or image.fingerprint in package.prior_image_fingerprints
        ):
            issues.append(
                _issue(
                    "IMAGE_USED_BEFORE",
                    "이전 콘텐츠에서 사용한 이미지입니다.",
                    card.page,
                )
            )
        if image.has_alpha and image.subject_box is None:
            issues.append(
                _issue(
                    "SUBJECT_BOX_MISSING",
                    "투명 이미지의 피사체 안전 영역이 계산되지 않았습니다.",
                    card.page,
                )
            )

    seen_images: set[str] = set()
    for card in sorted(package.cards, key=lambda item: item.page):
        image = images.get(card.image_id)
        if not image:
            continue
        if not is_editorially_composable(image):
            issues.append(
                _issue(
                    "IMAGE_ASSET_NOT_CARDWORTHY",
                    "가로 배너·장식용 자산은 카드 이미지로 쓸 수 없습니다. "
                    "장면이나 작품 비주얼을 다시 수집하세요.",
                    card.page,
                )
            )
        keys = image_visual_keys(image)
        is_reuse = bool(seen_images.intersection(keys))
        seen_images.update(keys)
        if is_reuse:
            issues.append(
                _issue(
                    "IMAGE_REUSED_RAW",
                    "같은 원본 이미지를 여러 카드의 주 이미지로 반복했습니다. "
                    "다른 공식 이미지를 수집해야 합니다.",
                    card.page,
                )
            )
    return issues


def check_copy(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    facts_by_id = {fact.id: fact for fact in package.facts}
    sources_by_id = {source.id: source for source in package.sources}

    def source_year(fact) -> int | None:
        years: set[int] = set()
        for source_id in fact.source_ids:
            source = sources_by_id.get(source_id)
            published_at = source.published_at if source else None
            if published_at:
                match = re.match(r"(20\d{2})", published_at.strip())
                if match:
                    years.add(int(match.group(1)))
        return next(iter(years)) if len(years) == 1 else None

    for card in package.cards:
        visible_copy = " ".join([card.eyebrow, *card.title_lines, *card.body_sentences])
        visible_dates = set(_date_in_quote(visible_copy))
        cited_dates = {
            parsed_date
            for fact_id in card.fact_ids
            if fact_id in facts_by_id and facts_by_id[fact_id].verified
            for parsed_date in _date_in_quote(
                # 사전 사실에는 기사 인용문이 없고 사실 문장 자체가 근거다
                str(facts_by_id[fact_id].details.get("source_quote") or facts_by_id[fact_id].statement),
                default_year=source_year(facts_by_id[fact_id]),
            )
        }
        if visible_dates - cited_dates:
            issues.append(
                _issue(
                    "UNSUPPORTED_DATE_CLAIM",
                    "카드에 쓴 날짜가 근거 사실에 없습니다. 날짜 사실을 다시 연결하거나 근거 없는 날짜를 지우세요.",
                    card.page,
                )
            )
    if not package.content_type:
        issues.append(
            _issue(
                "CONTENT_TYPE_MISSING",
                "추천 알고리즘용 대표 콘텐츠 유형을 지정해야 합니다.",

            )
        )
    if not POLICY.min_cards <= len(package.cards) <= POLICY.max_cards:
        issues.append(
            _issue("CARD_COUNT", f"카드는 {POLICY.min_cards}~{POLICY.max_cards}장이어야 합니다.")
        )
    if package.content_kind == ContentKind.INFORMATION:
        known_verified_ids = {fact.id for fact in package.facts if fact.verified}
        used_verified_ids = {
            fact_id
            for card in package.cards
            for fact_id in card.fact_ids
            if fact_id in known_verified_ids
        }
        if len(used_verified_ids) < 1:
            issues.append(
                _issue(
                    "FACT_DIVERSITY_LOW",
                    "카드뉴스에 활용된 검증 사실이 3개 미만입니다. 한 가지 사실을 늘려 쓰지 말고 "
                    "서로 다른 근거를 더 수집하거나 이 자료를 후보에서 제외하세요.",

                )
            )
    if package.cards:
        first = min(package.cards, key=lambda item: item.page)
        last = max(package.cards, key=lambda item: item.page)
        if first.role != "cover":
            issues.append(
                _issue(
                    "COVER_ROLE_MISSING",
                    "첫 장은 작품명과 핵심 훅을 담는 cover 역할이어야 합니다.",
                    first.page,
                )
            )
        if last.role != "ending":
            issues.append(
                _issue(
                    "ENDING_ROLE_MISSING",
                    "마지막 장은 앞선 소재를 회수하는 ending 역할이어야 합니다.",
                    last.page,
                )
            )
        # 11/12 서원: 소재 모음 표지 첫 줄은 둘째 줄('○○물 애니 모음' 등, 코드가 고정) 앞의 짧은 말
        plan_cover = next((p for p in package.card_plan if p.get("page") == first.page), {})
        suffix = plan_cover.get("title_suffix")
        if suffix and first.role == "cover":
            # 11/12 저녁 서원: 둘째 줄이 무슨 모음인지 밝히니 첫 줄은 재치 있게 써도 됨 → 길이만 본다
            head = first.title_lines[0].strip() if len(first.title_lines) > 1 else ""
            if not head or len(head.replace(" ", "")) > 15:
                issues.append(
                    _issue(
                        "COVER_MODIFIER_FORM",
                        f"표지 큰 글씨 첫 줄을 '{suffix}' 앞에 붙는 짧고 재치 있는 말로 다시 쓰세요 (15자 안쪽).",
                        first.page,
                    )
                )
        # 11/12 밤 서원: 주제가 노래 카드는 큰 글씨에 노래 제목만, 작은 글씨에 부른 사람 이름이 꼭 들어간다
        for page in package.card_plan:
            must = page.get("body_must_include")
            card = next((c for c in package.cards if c.page == page.get("page")), None)
            if must and card is not None and must not in " ".join(card.body_sentences):
                issues.append(
                    _issue(
                        "SONG_SINGER_MISSING",
                        f"작은 글씨 첫 문장에 부른 사람 이름 '{must}' 를 그대로 넣으세요.",
                        card.page,
                    )
                )
        # 11/12 서원: 사전 글 표지는 작은 설명까지 채운다 (프로필 · 이달의 생일만 비워도 됨)
        kind = request_kind(package.request_id)
        if (kind and kind not in COVER_BODY_OPTIONAL_KINDS and first.role == "cover"
                and not " ".join(first.body_sentences).strip()):
            issues.append(
                _issue(
                    "COVER_BODY_MISSING",
                    "표지 아래 작은 설명이 비었습니다. 큰 제목을 받쳐 주는 짧은 문장 하나를 "
                    "근거 사실 안에서 쓰세요.",
                    first.page,
                )
            )
    # 11/12 밤: 큰 글씨가 쉼표로 끝나며 끊긴 카드 ('하나코 군 노래,' 다음 줄이 빠짐)
    for card in package.cards:
        lines = [line.strip() for line in card.title_lines if line.strip()]
        if lines and lines[-1][-1] in ",，、·":
            issues.append(
                _issue(
                    "TITLE_DANGLING",
                    "큰 글씨가 쉼표로 끝나 문장이 끊겼습니다. 다음 줄을 채우거나 쉼표 없이 끝나게 쓰세요.",
                    card.page,
                )
            )

    fact_ids = {fact.id for fact in package.facts}
    for card in package.cards:
        title = card.plain_title
        body = " ".join(card.body_sentences).strip()
        # Language guardrails apply to reader-facing copy. Image explanations
        # are internal editorial metadata and can contain source-format punctuation.
        text = f"{card.eyebrow} {title} {body}".strip()
        if (
            package.content_type == "event"
            and len(re.findall(r"(?:20\d{2}\ub144\s*)?\d{1,2}\uc6d4\s*\d{1,2}\uc77c", title)) >= 2
        ):
            issues.append(
                _issue(
                    "EVENT_SCHEDULE_IN_TITLE",
                    "여러 행사 날짜를 제목에 몰아 넣지 마세요. 장소별 기간을 각각 짝지어 "
                    "상세 카드에 배치하세요.",
                    card.page,
                )
            )
        if card.page == 1:
            for term in package.term_glossary:
                if term.category == "work_title" and term.korean not in text:
                    issues.append(
                        _issue(
                            "WORK_TITLE_NOT_KOREAN",
                            f"첫 장에 작품의 한국어 제목이 없습니다: {term.korean}",
                            card.page,
                        )
                    )
        for term in package.term_glossary:
            if "クール" in term.original and re.search(r"쿠르", text):
                issues.append(
                    _issue(
                        "JAPANESE_TERM_MISTRANSLATED",
                        "애니메이션 분기 용어 クール은 ‘최종 쿨’처럼 ‘쿨’로 번역하세요 "
                        "(‘쿠르’ 금지).",
                        card.page,
                    )
                )
            if term.original not in text:
                continue
            original_at = text.find(term.original)
            korean_at = text.rfind(term.korean, 0, original_at)
            if korean_at < 0 or original_at - korean_at > max(45, len(term.korean) + 20):
                issues.append(
                    _issue(
                        "JAPANESE_TERM_UNPAIRED",
                        f"일본어 표기 앞에 자연스러운 한국어 번역을 붙이세요: "
                        f"{term.korean} ({term.original})",
                        card.page,
                    )
                )
        for match in re.finditer(r"[ぁ-ヿ]+|[、。]|[\u3400-\u9fff]+", text):
            japanese_run = match.group()
            if _is_japanese_hashtag_suffix(text, match.start()):
                continue
            if _has_korean_translation_before_japanese_original(text, match.start()):
                continue
            issues.append(
                _issue(
                    "JAPANESE_COPY_UNTRANSLATED",
                    "일본어 고유명사나 문장부호를 설명 없이 쓰지 마세요. 이름을 유지하려면 "
                    "한국어 표기를 먼저 쓰고 바로 뒤 괄호에 원문을 병기하고, 일반 문구는 "
                    "한국어로 옮기세요: "
                    f"{japanese_run}",
                    card.page,
                )
            )
        if not title and not body:
            issues.append(
                _issue(
                    "IMAGE_ONLY_PAGE",
                    "텍스트 없이 이미지만 있는 페이지입니다.",
                    card.page,
                )
            )
        fixed_title = any(int(p.get("page", 0)) == card.page and (p.get("title_lines") or p.get("title_prefix"))
                          for p in package.card_plan)  # 작품 · 노래 이름으로 고정한 제목은 줄일 수 없다
        if len(title) > POLICY.max_title_chars and not fixed_title:
            issues.append(
                _issue(
                    "TITLE_TOO_LONG",
                    "제목이 너무 길어 어절 단절 위험이 있습니다.",
                    card.page,
                )
            )
        if len(body) > POLICY.max_body_chars:
            issues.append(
                _issue(
                    "BODY_TOO_LONG",
                    "본문이 카드 한 장의 정보량을 초과했습니다.",
                    card.page,
                )
            )
        if any(term in text for term in AGENT_MONOLOGUE_TERMS):
            issues.append(
                _issue(
                    "AGENT_MONOLOGUE",
                    "독자에게 필요 없는 에이전트 혼잣말이 포함됐습니다.",
                    card.page,
                )
            )
        talk = _data_talk(text)
        if talk:
            issues.append(
                _issue(
                    "COPY_DATA_TALK",
                    f"자료 설명 말투가 카드에 남았습니다 ({talk}). 출처 이름 · 점수 · 기록 말투 없이 "
                    "사실 자체를 크리에이터 말투로 다시 쓰세요.",
                    card.page,
                )
            )
        if any(
            term in text
            for term in (
                "공식 사이트에서 확인",
                "배포 정보를 확인",
                "자세한 내용은 공식",
                "계속 지켜보자",
                "확인해 주세요",
                "확인하세요",
            )
        ):
            issues.append(
                _issue(
                    "RESEARCH_DELEGATED_TO_USER",
                    "확인 업무를 독자에게 넘깁니다. "
                    "확보한 사실을 직접 전달하거나 해당 내용을 제외해야 합니다.",
                    card.page,
                )
            )
        missing_facts = sorted(set(card.fact_ids) - fact_ids)
        if missing_facts:
            issues.append(
                _issue(
                    "COPY_UNGROUNDED",
                    f"근거 없는 fact_id가 있습니다: {missing_facts}",
                    card.page,
                )
            )
        for phrase in card.protected_phrases:
            if not _protected_phrase_is_present(phrase, text):
                issues.append(
                    _issue(
                        "PROTECTED_PHRASE_LOST",
                        f"보호 구절이 본문에 없습니다: {phrase}",
                        card.page,
                    )
                )
            elif not any(phrase in line for line in card.title_lines) and phrase in title:
                issues.append(
                    _issue(
                        "PROTECTED_PHRASE_SPLIT",
                        f"보호 구절이 제목 줄 사이에서 갈라졌습니다: {phrase}",
                        card.page,
                    )
                )

    if package.cards:
        # Cover and ending may intentionally tease/recap a central verified fact.
        # Exact fact-set duplication is only a defect between teaching cards.
        interior_cards = [card for card in package.cards if card.role not in {"cover", "ending"}]
        seen_signatures: set[tuple[str, ...]] = set()
        for card in interior_cards:
            signature = tuple(sorted(card.fact_ids))
            if signature and signature in seen_signatures:
                issues.append(
                    _issue(
                        "CARD_REDUNDANT",
                        "앞 카드와 근거 사실이 중복됩니다.",
                        card.page,
                    )
                )
            seen_signatures.add(signature)
        if package.content_kind.value == "information":
            # Cover/ending may recap the central fact, but consecutive teaching
            # cards must contribute distinct evidence rather than paraphrasing it.
            interior_cards = [
                card for card in package.cards if card.role not in {"cover", "ending"}
            ]
            seen_interior_facts: set[str] = set()
            for card in interior_cards:
                repeated = seen_interior_facts.intersection(card.fact_ids)
                if repeated:
                    issues.append(
                        _issue(
                            "CARD_FACT_REUSED",
                            "중간 카드가 앞 카드의 근거 사실을 반복합니다. "
                            "새 근거·새 정보가 있는 카드로 다시 구성하세요.",
                            card.page,
                        )
                    )
                seen_interior_facts.update(card.fact_ids)
        ending = package.cards[-1]
        ending_text = f"{ending.plain_title} {' '.join(ending.body_sentences)}".strip()
        if ending.ending_joke:
            if len(ending_text) > POLICY.max_ending_chars:
                issues.append(
                    _issue(
                        "ENDING_TOO_LONG",
                        f"마지막 장의 제목과 본문 합계가 {POLICY.max_ending_chars}자를 넘었습니다. "
                        "핵심 사실은 유지하고 엔딩 문구를 짧게 다듬으세요.",
                        ending.page,

                    )
                )
            if any(term in ending_text for term in GENERIC_HUMOR_TERMS):
                issues.append(
                    _issue(
                        "ENDING_GENERIC",
                        "작품명만 바꿔도 성립하는 상투적 드립입니다.",
                        ending.page,
                    )
                )
            if any(mark in ending_text for mark in EXCESSIVE_PUNCTUATION):
                issues.append(
                    _issue(
                        "ENDING_LOUD",
                        "데드팬 결말에 과한 감탄·웃음 표시가 있습니다.",
                        ending.page,
                    )
                )
    return issues


def check_layout(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for card in package.cards:
        layout = card.layout
        image = next((item for item in package.images if item.id == card.image_id), None)
        boxes = (layout.image_box, layout.copy_box, layout.text_overlay_box)
        if any(
            box.x < 0 or box.y < 0 or box.x + box.width > 1.001 or box.bottom > 1.001
            for box in boxes
        ):
            issues.append(
                _issue(
                    "BOX_OVERFLOW",
                    "이미지 또는 글자 영역이 카드 밖으로 나갑니다.",
                    card.page,
                )
            )
        integrated_overlay_layout = layout.copy_box == layout.text_overlay_box
        if (
            not integrated_overlay_layout
            and layout.template
            not in {
                "full_bleed",
                "topwide",
                "ending",
                "cover_blend",
                "cover_immersive",
                "detail_blend",
                "detail_immersive",
            }
            and (
                layout.image_box.x < 0.03
                or layout.image_box.x + layout.image_box.width > 0.97
                or layout.image_box.y < 0.03
                or layout.image_box.bottom > 0.97
            )
        ):
            issues.append(
                _issue(
                    "EDGE_ATTACHED_IMAGE",
                    "일반 카드의 이미지가 화면 가장자리에 너무 붙어 있습니다.",
                    card.page,
                )
            )
        text_region = layout.text_overlay_box
        is_cover = card.page == 1 or card.role == "cover"
        invalid_region = (
            text_region.width < 0.30
            or text_region.height < 0.18
            or text_region.x + text_region.width > 1.001
            or text_region.bottom > 1.001
            or (
                is_cover
                and not (
                    text_region.y < 0.40
                    or text_region.y >= 0.60
                )
            )
        )
        if invalid_region:
            issues.append(
                _issue(
                    "TEXT_OVERLAY_SAFE_AREA_INVALID",
                    "HTML 텍스트 영역이 너무 작거나 카드 밖에 있습니다. "
                    "커버는 상단 또는 하단 영역을 사용해야 합니다.",
                    card.page,
                )
            )
        overlaps = not (
            layout.image_box.x + layout.image_box.width <= text_region.x
            or text_region.x + text_region.width <= layout.image_box.x
            or layout.image_box.bottom <= text_region.y
            or text_region.bottom <= layout.image_box.y
        )
        # Only the cover intentionally lets the image bleed behind the bottom copy.
        if overlaps and not is_cover and layout.template.startswith("editorial_"):
            issues.append(
                _issue(
                    "IMAGE_COPY_REGION_OVERLAP",
                    "커버 이외 페이지에서 이미지 영역과 HTML 텍스트 영역이 겹칩니다.",
                    card.page,
                )
            )
        gap = layout.copy_box.y - layout.image_box.bottom
        if card.role == "cover" and gap > POLICY.max_cover_vertical_gap:
            issues.append(
                _issue(
                    "COVER_EMPTY_GAP",
                    f"표지 이미지와 제목 사이 여백이 큽니다({gap:.2f}).",
                    card.page,
                )
            )
        if (
            layout.fit_mode not in {"contain", "contain_blur"}
            or layout.image_scale > 1.0
            or abs(layout.image_offset_y) > 0.001
        ):
            issues.append(
                _issue(
                    "IMAGE_CROP_RISK",
                    "이미지 잘림 가능성이 있는 배치입니다. "
                    "원본 비율·크기·중앙 배치로 수정해야 합니다.",
                    card.page,
                )
            )
        if layout.contrast_mode not in {"dark_on_light", "light_on_dark", "scrim_required"}:
            issues.append(
                _issue(
                    "CONTRAST_UNKNOWN",
                    "텍스트 대비 방식이 지정되지 않았습니다.",
                    card.page,
                )
            )
        if (
            image
            and image.subject_box
            and (
                layout.fit_mode not in {"contain", "contain_blur"}
                or layout.image_scale > 1.0
                or abs(layout.image_offset_y) > 0.001
            )
        ):
            box = image.subject_box
            scale = layout.image_scale
            offset = layout.image_offset_y
            projected_top = 0.5 + (box.y - 0.5) * scale + offset
            projected_bottom = 0.5 + (box.bottom - 0.5) * scale + offset
            projected_left = 0.5 + (box.x - 0.5) * scale
            projected_right = 0.5 + (box.x + box.width - 0.5) * scale
            if (
                min(projected_top, projected_left) < 0.03
                or max(projected_bottom, projected_right) > 0.97
            ):
                issues.append(
                    _issue(
                        "SUBJECT_CLIPPED",
                        "확대·이동 뒤 피사체의 머리, 발 또는 좌우 끝이 안전 영역을 벗어납니다.",
                        card.page,
                    )
                )
    return issues


def detail_body_contamination(package: ContentPackage, body: str | None = None) -> list[str]:
    """상세 글에 섞인 것 (없으면 빈 목록): 보이지 않는 글자, 다른 문자권 글자, 지시문 · 사실 번호 · 꾸밈 기호,
    사실 · 카드 · 참고 글 어디에도 없는 영어 낱말 (작품 · 노래 이름처럼 사실에 있는 영어는 괜찮다)"""
    text = package.detail_body if body is None else body
    found = []
    if INVISIBLE_CHARS.search(text):
        found.append("보이지 않는 글자")
    found += [f"다른 문자 '{m.group(0)}'" for m in list(_FOREIGN_SCRIPT.finditer(text))[:3]]
    found += [f"'{m.group(0)}'" for m in list(_DETAIL_LEAK.finditer(text))[:3]]
    corpus = " ".join([
        package.topic,
        *(fact.statement for fact in package.facts),
        *(str(value) for fact in package.facts for key, value in fact.details.items()
          if key.endswith("_reference_do_not_copy") or key == "source_quote"),
        *(" ".join([*card.title_lines, *card.body_sentences]) for card in package.cards),
        *(f"{term.original} {term.korean}" for term in package.term_glossary),
    ]).lower()
    stray = sorted({word for word in _LATIN_WORD.findall(text)
                    if word.lower() not in corpus and word.lower() not in _LATIN_OK})
    if stray:
        found.append("사실에 없는 영어 " + ", ".join(stray[:5]))
    covered: set[int] = set()
    for match in _LENTICULAR.finditer(text):
        covered.update(range(match.start(), match.end()))
        if match.group(0).lower() not in corpus:
            found.append(f"'{match.group(0)}'")
    found += [f"'{m.group(0)}'" for m in re.finditer(r"[【】]", text) if m.start() not in covered][:2]
    return found


_COPY_RUN = 20  # 공백 뺀 글자 수


def _squash(text: str | None) -> str:
    return re.sub(r"\s", "", text or "")


def detail_body_copied(package: ContentPackage) -> list[str]:
    """참고 글과 공백 빼고 20자 넘게 똑같이 이어지는 구절 (11/11: 나무위키 약관상 문장을 옮기지 않는다).
    사실 문장 · 카드 · 용어집에도 있는 구절(긴 작품명 등)은 빼고 본다"""
    refs = [_squash(str(value)) for fact in package.facts for key, value in fact.details.items()
            if key.endswith("_reference_do_not_copy") and value]
    if not refs:
        return []
    known = _squash(" ".join([
        *(fact.statement for fact in package.facts),
        *(" ".join([*card.title_lines, *card.body_sentences]) for card in package.cards),
        *(term.korean for term in package.term_glossary),
    ]))
    body = _squash(package.detail_body)
    found, i = [], 0
    while i <= len(body) - _COPY_RUN:
        window = body[i:i + _COPY_RUN]
        if window not in known and any(window in ref for ref in refs):
            j = i + _COPY_RUN
            while j < len(body) and any(body[i:j + 1] in ref for ref in refs):
                j += 1
            found.append(body[i:j])
            i = j
        else:
            i += 1
    return found


def check_detail_body(package: ContentPackage) -> list[QualityIssue]:
    """Ensure detail copy is distinct, cited, and grounded in package evidence."""
    if not package.detail_body:
        return []
    issues: list[QualityIssue] = []
    if not POLICY.min_detail_chars <= len(package.detail_body.strip()) <= POLICY.max_detail_chars:
        issues.append(
            _issue(
                "DETAIL_BODY_LENGTH",
                f"자세히 보기 글은 {POLICY.min_detail_chars}~{POLICY.max_detail_chars}자 범위여야 합니다.",

            )
        )
    # 유형별 길이 (공백 뺀 글자 수, 11/11 서원). 조금 넘치거나 모자란 건 봐준다
    low, high = detail_length_range(
        package.request_id,
        any(fact.fact_type == "namu_character_reference" for fact in package.facts),
    )
    chars = len(re.sub(r"\s", "", package.detail_body))
    if not int(low * 0.9) <= chars <= int(high * 1.15):
        issues.append(
            _issue(
                "DETAIL_BODY_LENGTH",
                f"자세히 보기 글은 공백 빼고 {low}~{high}자로 씁니다 (지금 {chars}자).",
            )
        )
    dirt = detail_body_contamination(package)
    if dirt:
        issues.append(
            _issue(
                "DETAIL_BODY_CONTAMINATED",
                "상세 글에 본문이 아닌 글자가 섞였습니다 (" + "; ".join(dirt) + "). "
                "메모 · 지시문 · 사실 번호 없이 한국어 본문만 다시 쓰세요.",
            )
        )
    stock = sorted({match.group(0) for match in _DETAIL_STOCK.finditer(package.detail_body)})
    stock += [f"'{word}' {package.detail_body.count(word)}번" for word in ("셈이", "재밌")
              if package.detail_body.count(word) >= 2]
    # 생일 글이 날짜 · 달력 얘기로 분량을 채우던 것 (11/11 시범: 〈주술회전〉 생일 모음이 달력 · 출석부 비유만 반복)
    kind = (package.request_id or "").split("-")[2] if (package.request_id or "").count("-") >= 2 else ""
    calendar = sum(package.detail_body.count(word) for word in ("달력", "출석부", "날짜 간격"))
    if kind in ("bdaybook", "bdaymonth") and calendar >= 2:
        stock.append(f"달력 · 출석부 얘기 {calendar}번 (날짜는 한 번만 말하고 캐릭터의 성격 · 버릇 · 관계로 채우세요)")
    if kind in ("bdaybook", "bdaymonth"):
        # 11/11 전체 실행 첫 결과: '달력' 낱말만 피하고 "10월만 유독 바빠져"처럼 같은 달 얘기를 되풀이함
        months = re.findall(r"(\d{1,2})\s?월", package.detail_body)
        if len(months) > len(set(months)) + 1:
            stock.append(f"같은 달 얘기 되풀이 ({len(months)}번, 생일 날짜는 한 번씩만 말하세요)")
        reference_ids = {fact.id for fact in package.facts
                         if any(key.endswith("_reference_do_not_copy") and value for key, value in fact.details.items())}
        if reference_ids and not reference_ids & set(package.detail_fact_ids):
            stock.append("캐릭터 참고 글을 하나도 안 씀 (성격 · 버릇 · 관계를 참고 글에서 가져와 채우세요)")
    # 11/11: 원작 이야기 · 입문 가이드가 권수 · 화수 · 날짜만 늘어놓고 붙여 준 나무위키 문단을 안 씀 (〈전생슬〉 · 〈최애의 아이〉)
    if kind in ("origin", "basic", "intro", "bdaybook", "bdaymonth"):
        namu_ids = {fact.id for fact in package.facts if fact.fact_type.startswith("namu_")}
        if namu_ids and not namu_ids & set(package.detail_fact_ids):
            stock.append("붙여 준 나무위키 문단을 하나도 안 씀 (특징 · 평가 · 뒷이야기 · 성격을 한 문단은 거기서 가져오세요)")
    if kind in ("origin", "basic"):
        # 날짜 하나("2014년 5월 30일")는 숫자 하나로 센다
        numbers = re.findall(r"\d+", re.sub(r"\d{4}\s?년\s?\d{1,2}\s?월\s?\d{1,2}\s?일", "0", package.detail_body))
        if len(numbers) > 8:
            stock.append(f"숫자 나열 ({len(numbers)}개, 연도 · 권수 · 화수는 한 문단에 몰아서 한 번씩만)")
    if stock:
        issues.append(
            _issue(
                "DETAIL_BODY_STOCK_PHRASE",
                f"상투 문구 · 같은 맺음말 반복을 빼고 구체적인 사실이나 반응으로 다시 쓰세요: {', '.join(stock)}",
            )
        )
    swear = sorted({match.group(0) for match in _PROFANITY.finditer(package.detail_body)})
    if swear:
        issues.append(
            _issue(
                "DETAIL_BODY_PROFANITY",
                f"욕설은 쓰지 않습니다: {', '.join(swear)}. 센 입말은 괜찮지만 욕 없이 다시 쓰세요.",
            )
        )
    copied = detail_body_copied(package)
    if copied:
        issues.append(
            _issue(
                "DETAIL_BODY_COPIED",
                "참고 글을 그대로 옮긴 구절이 있습니다: " + " / ".join(f"'{c[:40]}'" for c in copied[:3])
                + ". 같은 사실을 네 말과 순서로 다시 쓰세요.",
            )
        )
    talk = _data_talk(package.detail_body)
    if talk:
        issues.append(
            _issue(
                "DETAIL_BODY_DATA_TALK",
                f"상세 글에 자료 설명 말투가 남았습니다 ({talk}). 출처 이름 · 점수 · 기록 말투 없이 "
                "사실 자체를 크리에이터 말투로 다시 쓰세요.",
            )
        )
    facts_by_id = {fact.id: fact for fact in package.facts}
    source_ids = {source.id for source in package.sources}
    card_fact_ids = {fact_id for card in package.cards for fact_id in card.fact_ids}
    unlinked_card_facts = card_fact_ids - set(package.detail_fact_ids)
    if unlinked_card_facts:
        issues.append(
            _issue(
                "DETAIL_BODY_FACT_LINK_MISSING",
                "Detail article is missing card fact IDs: "
                + ", ".join(sorted(unlinked_card_facts)),

            )
        )
    unknown_fact_ids = sorted(set(package.detail_fact_ids) - facts_by_id.keys())
    if not package.detail_fact_ids or unknown_fact_ids:
        issues.append(
            _issue(
                "DETAIL_BODY_FACT_LINK_MISSING",
                "상세글에 연결된 근거 팩트가 없거나 존재하지 않는 팩트 ID가 있습니다.",

            )
        )
    for fact_id in set(package.detail_fact_ids) & facts_by_id.keys():
        fact = facts_by_id[fact_id]
        if package.content_kind == ContentKind.INFORMATION and not fact.verified:
            issues.append(
                _issue(
                    "DETAIL_BODY_FACT_UNVERIFIED",
                    f"상세글이 미검증 정보 팩트를 참조합니다: {fact_id}",

                )
            )
        if not set(fact.source_ids) & source_ids:
            issues.append(
                _issue(
                    "DETAIL_BODY_SOURCE_MISSING",
                    f"상세글 근거 팩트에 연결된 출처가 없습니다: {fact_id}",

                )
            )
    expected_source_ids = {
        source_id
        for fact_id in set(package.detail_fact_ids) & facts_by_id.keys()
        for source_id in facts_by_id[fact_id].source_ids
        if source_id in source_ids
    }
    if not expected_source_ids or not expected_source_ids.issubset(package.detail_source_ids):
        issues.append(
            _issue(
                "DETAIL_BODY_SOURCE_LINK_MISSING",
                "상세글에 연결된 근거 출처 목록이 불완전합니다.",

            )
        )
    return issues


def run_quality_checks(package: ContentPackage) -> list[QualityIssue]:
    """코드로 확인 가능한 모든 문제를 안정적인 순서로 반환합니다."""
    return [
        *check_sources(package),
        *check_images(package),
        *check_copy(package),
        *check_layout(package),
        *check_detail_body(package),
    ]


SCORE_THRESHOLDS = {
    "pass": POLICY.quality_pass_score,
    "card_minimum": POLICY.quality_card_minimum_score,
    "safety_minimum_per_card": POLICY.safety_minimum_per_card,
}

FACT_BLOCKERS = {
    "SOURCE_NOT_HTTPS", "FACT_SOURCE_MISSING", "FACT_UNVERIFIED",
    "CAMPAIGN_DETAILS_MISSING", "COPY_UNGROUNDED", "SOURCE_STALE", "EVENT_ALREADY_ENDED",
    "FACT_DIVERSITY_LOW", "UNSUPPORTED_DATE_CLAIM", "SOURCE_PUBLICATION_DATE_SHOWN",
    "EVENT_PERIOD_MISSING", "GOODS_RELEASE_PERIOD_MISSING",
    "MULTIPLE_GOODS_RELEASE_DATES",
}
IMAGE_ISSUE_CODES = {
    "IMAGE_MISSING",
    "IMAGE_RENDER_MISSING",
    "IMAGE_LOW_RESOLUTION",
    "IMAGE_NOT_OFFICIAL",
    "IMAGE_DOWNLOAD_FAILED",
    "IMAGE_FACT_MISMATCH",
    "IMAGE_USED_BEFORE",
    "SUBJECT_BOX_MISSING",
    "IMAGE_RIGHTS_REVIEW",
    "IMAGE_REUSED_RAW",
    "IMAGE_SOURCE_PAGE_MISMATCH",
    "IMAGE_ASSET_NOT_CARDWORTHY",
    "IMAGE_VISION_MISMATCH",
    "IMAGE_COMPOSITION_REJECTED",
    "IMAGE_VISION_REVIEW_MISSING",
    "COVER_SOURCE_IMAGE_MISSING",
    "SOURCE_IMAGE_MISSING",
}
def score_quality(
    package: ContentPackage,
    issues: list[QualityIssue],
    *,
    language_score: int | None = None,
    safety_score: int | None = None,
    card_language_scores: dict[int, int] | None = None,
    thresholds: dict[str, int] | None = None,
) -> dict[str, object]:
    """Score deterministic checks and a separate reviewer language assessment."""
    active_thresholds = thresholds or SCORE_THRESHOLDS
    hard_blockers, actionable_issues, routes_to_crawler = decision_metadata(issues)
    scored_issues = [issue for issue in issues if RULES[issue.code].severity != "warning"]
    fact_issues = [
        issue for issue in scored_issues
        if issue.code in FACT_BLOCKERS and RULES[issue.code].severity == "blocker"
    ]

    evidence_score = 25 if not fact_issues else 0
    used_image_ids = {card.image_id for card in package.cards}
    risk_accepted_count = sum(
        image.id in used_image_ids
        and image.rights_status.value
        in {"official_public_risk_accepted", "public_editorial_risk_accepted"}
        for image in package.images
    )
    source_image_card_count = sum(card.visual_mode == "source_image" for card in package.cards)
    image_issues = [
        issue
        for issue in scored_issues
        if issue.code in IMAGE_ISSUE_CODES and RULES[issue.code].severity == "blocker"
    ]
    image_score = (
        0
        if image_issues
        else 8
        if source_image_card_count == 0
        else 12
        if risk_accepted_count
        else 15
    )
    crop_codes = {"IMAGE_CROP_RISK", "SUBJECT_CLIPPED"}
    image_visibility_score = (
        0
        if any(
            issue.code in crop_codes and RULES[issue.code].severity == "blocker"
            for issue in scored_issues
        )
        else 10
    )
    readability_score = 15
    editorial_score = 5
    scores = {
        "evidence_and_accuracy": evidence_score,
        "image_suitability_and_rights": image_score,
        "image_full_visibility": image_visibility_score,
        "readability_and_layout": readability_score,
        "korean_naturalness_and_translation": language_score or 0,
        "structure_and_style_rules": editorial_score,
        "safety_and_respect": safety_score or 0,
    }
    revision_deductions = sum(
        score_penalty(issue.code)
        for issue in issues
        if RULES[issue.code].severity == "revise"
    )
    total = max(0, sum(scores.values()) - revision_deductions)
    visual_mode_counts = {
        "source_image": sum(card.visual_mode == "source_image" for card in package.cards),
        "editorial_graphic": sum(card.visual_mode == "editorial_graphic" for card in package.cards),
    }

    card_scores: dict[str, int] = {}
    for card in package.cards:
        page_issues = [issue for issue in issues if issue.page == card.page]
        penalty = sum(
            score_penalty(issue.code)
            for issue in page_issues
            if RULES[issue.code].severity == "revise"
        )
        if any(RULES[issue.code].severity == "blocker" for issue in page_issues):
            penalty = max(penalty, 30)
        rule_score = max(0, 100 - penalty)
        language_card_score = (card_language_scores or {}).get(card.page)
        card_scores[str(card.page)] = min(
            rule_score,
            language_card_score if language_card_score is not None else rule_score,
        )

    lowest_card = min(card_scores.values(), default=100)
    warnings = sorted(
        issue.code for issue in issues if RULES[issue.code].severity == "warning"
    )
    if hard_blockers and routes_to_crawler:
        decision = "return_to_material"
    elif hard_blockers:
        decision = "revise"
    elif language_score is None:
        decision = "pending_language_review"
    elif (
        total < active_thresholds["pass"]
        or lowest_card < active_thresholds["card_minimum"]
    ):
        decision = "return_to_material" if routes_to_crawler else "revise"
    else:
        decision = "pass_candidate"

    return {
        "total": total,
        "maximum": 100,
        "revision_deductions": revision_deductions,
        "thresholds": active_thresholds,
        "dimensions": scores,
        "accepted_image_risk_count": risk_accepted_count,
        "visual_mode_counts": visual_mode_counts,
        "accepted_image_risk_note": (
            "공개 이미지 출처를 확인했으나 사용 허가를 확인한 것은 아닙니다."
            if risk_accepted_count
            else ""
        ),
        "card_scores": card_scores,
        "lowest_card_score": lowest_card,
        "hard_blockers": hard_blockers,
        "actionable_issues": actionable_issues,
        "warnings": warnings,
        "routes_to_crawler": routes_to_crawler,
        "decision": decision,
        "note": (
            "이미지는 원본 비율로 전부 보여야 합니다. "
            "크롭·확대·이동 위험은 점수와 무관한 필수 차단 항목입니다. "
            "사실·권리·레이아웃·안전의 치명적 결함도 총점으로 상쇄할 수 없습니다. "
            "안전·존중 점수는 10점입니다. 카드별 기준 미달과 혐오·성적 대상화 blocker는 "
            "수정 반환합니다. "
            "팬덤 흥미도는 검수 LLM의 fan_engagement 항목으로 평가합니다."
        ),
    }
