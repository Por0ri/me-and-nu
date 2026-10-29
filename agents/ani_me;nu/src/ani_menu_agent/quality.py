"""LLM을 쓰지 않고도 매번 동일하게 적용되는 품질 검사."""

import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from ani_menu_agent.classification import infer_strong_content_type
from ani_menu_agent.image_selection import (
    image_belongs_to_package_sources,
    image_visual_key,
    image_visual_keys,
    is_editorially_composable,
)
from ani_menu_agent.models import ContentKind, ContentPackage, QualityIssue
from ani_menu_agent.policy import (
    AGENT_MONOLOGUE_TERMS,
    EXCESSIVE_PUNCTUATION,
    GENERIC_HUMOR_TERMS,
    POLICY,
)
from ani_menu_agent.review_rules import RULES, decision_metadata, make_issue, score_penalty


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
    from ani_menu_agent.extraction import _date_in_quote

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
    official_count = sum(source.is_official for source in package.sources)
    editorial_count = sum(source.role == "editorial" for source in package.sources)
    reference_count = sum(source.role == "reference" for source in package.sources)
    editorial_content_type = package.content_type in {"review", "curation", "humor"}
    reference_content_type = package.content_type in {"guide", "people", "character", "curation"}
    event_dates = {
        parsed
        for value in (package.event_start, package.event_end)
        if value
        for parsed, _variants in [_publication_date_variants(value)]
        if parsed
    }
    if (
        package.content_kind == ContentKind.INFORMATION
        and official_count < POLICY.min_official_sources
        and not (editorial_content_type and editorial_count > 0)
        and not (reference_content_type and reference_count > 0)
    ):
        issues.append(
            _issue(
                "SOURCE_NO_OFFICIAL",
                "정보 콘텐츠는 공식 1차 출처가 필요하며, 리뷰·큐레이션·사실형 유머는 "
                "허용된 편집 출처를 사용할 수 있습니다.",

            )
        )
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
            from ani_menu_agent.extraction import _date_in_quote

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
    from ani_menu_agent.extraction import _date_in_quote

    issues: list[QualityIssue] = []
    used_fact_ids = {fact_id for card in package.cards for fact_id in card.fact_ids}
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
                str(facts_by_id[fact_id].details.get("source_quote") or ""),
                default_year=source_year(facts_by_id[fact_id]),
            )
        }
        if visible_dates - cited_dates:
            issues.append(
                _issue(
                    "UNSUPPORTED_DATE_CLAIM",
                    "移대뱶???곸뿭???ъ슜???좎쭨瑜?洹쇨굅 fact_id濡?媛由ы궎?꽌?쇰줈 ?쇱튂?섏? "
                    "?딆뒿?덈떎. ?좎쭨 ?ъ떎瑜??ㅼ떆 ?몄슜?섍굅??湲곗? ?녿뒗 ?좎쭨瑜?젣嫄고븯?몄슂.",
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
    evidence_type = infer_strong_content_type(package)
    if evidence_type and package.content_type != evidence_type:
        issues.append(
            _issue(
                "CONTENT_TYPE_MISMATCH",
                f"검증된 자료 유형은 {evidence_type}에 가깝지만 현재 분류는 "
                f"{package.content_type or '미분류'}입니다. 자료 목적에 맞게 다시 분류하세요.",

            )
        )
    if package.content_type == "event":
        event_facts = [fact for fact in package.facts if fact.verified]
        has_explicit_period = any(
            len(_date_in_quote(str(fact.details.get("source_quote") or ""))) >= 2
            for fact in event_facts
        )
        if has_explicit_period and (not package.event_start or not package.event_end):
            issues.append(
                _issue(
                    "EVENT_PERIOD_MISSING",
                    "원문에 행사 기간의 양 끝 날짜가 있으므로 시작일과 종료일을 모두 "
                    "확인해 DB에 저장해야 합니다.",

                )
            )
        has_venue_fact = any(
            fact.fact_type.strip().lower() in {"event_location", "event_venue", "venue", "location"}
            for fact in event_facts
        )
        if has_venue_fact and not package.event_location:
            issues.append(
                _issue(
                    "EVENT_LOCATION_MISSING",
                    "검증된 행사 장소가 있으므로 장소명을 구조화해 DB에 저장해야 합니다.",

                )
            )
        if has_venue_fact and not package.event_country:
            issues.append(
                _issue(
                    "EVENT_COUNTRY_MISSING",
                    "검증된 행사 장소의 국가를 확인해 DB에 저장해야 합니다.",

                )
            )
        if has_venue_fact and package.event_region not in {"domestic", "overseas"}:
            issues.append(
                _issue(
                    "EVENT_REGION_MISSING",
                    "검증된 행사 장소를 한국 기준 국내/해외로 분류해 DB에 저장해야 합니다.",

                )
            )
    if package.content_type in {"event", "goods"}:
        for fact in package.facts:
            fact_type = fact.fact_type.strip().lower()
            if (
                not fact.verified
                or fact.id not in used_fact_ids
                or fact_type
                not in {
                    "event_schedule",
                    "event_period",
                    "event_date",
                    "product_release",
                    "release",
                    "release_date",
                    "goods_release",
                    "sales_start",
                }
            ):
                continue
            quote = fact.details.get("source_quote")
            if not isinstance(quote, str):
                continue
            cited_dates = _date_in_quote(quote)
            fact_statement = fact.statement.casefold()
            quote_folded = quote.casefold()
            start_or_end_only_markers = (
                "시작",
                "부터",
                "개시",
                "スタート",
                "開始",
                "より",
                "から",
                "까지",
                "まで",
                "until",
                "through",
                "start",
                "starting",
                "from",
            )
            if (
                fact_type in {"event_schedule", "event_period"}
                and len(cited_dates) == 1
                and any(
                    marker.casefold() in fact_statement or marker.casefold() in quote_folded
                    for marker in start_or_end_only_markers
                )
            ):
                # A one-date schedule quote can be only the start or end of a
                # longer verified period; do not reinterpret it as a one-day event.
                continue
            if len(cited_dates) == 1 and (
                package.event_start != cited_dates[0] or package.event_end != cited_dates[0]
            ):
                issues.append(
                    _issue(
                        "SINGLE_DAY_PERIOD_NOT_MIRRORED",
                        "원문이 하루 일정/발매일을 가리키므로 event_start와 event_end에 "
                        "같은 날짜를 저장하세요.",

                    )
                )
                break
    if package.content_type == "goods":
        from ani_menu_agent.extraction import _date_in_quote

        release_periods: set[tuple[str, str]] = set()
        all_verified_release_dates: set[str] = set()
        for fact in package.facts:
            if not fact.verified:
                continue
            if fact.fact_type.strip().lower() not in {
                "release",
                "product_release",
                "release_date",
                "goods_release",
                "sales_start",
            }:
                continue
            dates = sorted(set(_date_in_quote(str(fact.details.get("source_quote") or ""))))
            all_verified_release_dates.update(dates)
            if fact.id not in used_fact_ids:
                continue
            if len(dates) == 1:
                release_periods.add((dates[0], dates[0]))
            elif len(dates) >= 2:
                release_periods.add((dates[0], dates[-1]))
        if len(release_periods) > 1:
            issues.append(
                _issue(
                    "MULTIPLE_GOODS_RELEASE_DATES",
                    "하나의 굿즈 콘텐츠에 서로 다른 발매일이 섞였습니다. "
                    "같은 발매일의 상품만 남기고 "
                    "그 날짜를 시작일·종료일에 똑같이 저장하세요.",

                )
            )
        if all_verified_release_dates and (not package.event_start or not package.event_end):
            issues.append(
                _issue(
                    "GOODS_RELEASE_PERIOD_MISSING",
                    "怨듭떇 ?먮Ц???곹뭹 諛쒕ℓ?쇱씠 ?뺤씤?섏뿀?쇰?濡?goods "
                    "?좎쭨瑜?DB ???ν빐???⑸땲??",

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
        if len(title) > POLICY.max_title_chars:
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


def check_detail_body(package: ContentPackage) -> list[QualityIssue]:
    """Ensure detail copy is distinct, cited, and grounded in package evidence."""
    if not package.detail_body:
        return []
    issues: list[QualityIssue] = []
    if not 180 <= len(package.detail_body.strip()) <= 3000:
        issues.append(
            _issue(
                "DETAIL_BODY_LENGTH",
                "자세히 보기 글은 180~3000자 범위여야 합니다.",

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
    "SOURCE_NO_OFFICIAL", "SOURCE_NOT_HTTPS", "FACT_SOURCE_MISSING", "FACT_UNVERIFIED",
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
        decision = "return_to_crawler"
    elif hard_blockers:
        decision = "revise"
    elif language_score is None:
        decision = "pending_language_review"
    elif (
        total < active_thresholds["pass"]
        or lowest_card < active_thresholds["card_minimum"]
    ):
        decision = "return_to_crawler" if routes_to_crawler else "revise"
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
