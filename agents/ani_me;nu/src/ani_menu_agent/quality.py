"""LLM을 쓰지 않고도 매번 동일하게 적용되는 품질 검사."""

import re
from collections import Counter
from urllib.parse import urlparse

from ani_menu_agent.image_selection import image_coverage_issue, image_visual_key
from ani_menu_agent.models import ContentKind, ContentPackage, QualityIssue
from ani_menu_agent.policy import (
    AGENT_MONOLOGUE_TERMS,
    EXCESSIVE_PUNCTUATION,
    GENERIC_HUMOR_TERMS,
    POLICY,
)


def _issue(code: str, message: str, page: int | None = None, owner: str = "editor") -> QualityIssue:
    return QualityIssue(code=code, message=message, page=page, owner=owner)


def check_sources(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    source_ids = {source.id for source in package.sources}
    used_fact_ids = {fact_id for card in package.cards for fact_id in card.fact_ids}
    official_count = sum(source.is_official for source in package.sources)
    if (
        package.content_kind == ContentKind.INFORMATION
        and official_count < POLICY.min_official_sources
    ):
        issues.append(
            _issue(
                "SOURCE_NO_OFFICIAL", "공식 1차 출처가 한 곳 이상 필요합니다.", owner="fact_checker"
            )
        )
    for source in package.sources:
        if urlparse(str(source.url)).scheme != "https":
            issues.append(
                _issue("SOURCE_NOT_HTTPS", f"HTTPS가 아닌 출처: {source.url}", owner="fact_checker")
            )
    for fact in package.facts:
        missing = sorted(set(fact.source_ids) - source_ids)
        if missing:
            issues.append(
                _issue(
                    "FACT_SOURCE_MISSING",
                    f"사실 {fact.id}의 출처가 없습니다: {missing}",
                    owner="fact_checker",
                )
            )
        if package.content_kind == ContentKind.INFORMATION and not fact.verified:
            issues.append(
                _issue("FACT_UNVERIFIED", f"미검증 사실: {fact.statement}", owner="fact_checker")
            )
        if fact.fact_type == "campaign" and fact.id in used_fact_ids:
            required = {"participation_steps", "prize", "deadline", "region"}
            missing = sorted(key for key in required if not fact.details.get(key))
            if missing:
                issues.append(
                    _issue(
                        "CAMPAIGN_DETAILS_MISSING",
                        f"응모 정보의 필수 항목이 빠졌습니다: {', '.join(missing)}",
                        owner="fact_checker",
                    )
                )
    return issues


def check_images(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    images = {image.id: image for image in package.images}
    used: list[str] = []
    for card in package.cards:
        image = images.get(card.image_id)
        if not image:
            issues.append(
                _issue(
                    "IMAGE_MISSING",
                    f"이미지 {card.image_id}를 찾을 수 없습니다.",
                    card.page,
                    "copy_writer",
                )
            )
            continue
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
            if upscale > POLICY.max_image_upscale:
                issues.append(
                    _issue(
                        "IMAGE_LOW_RESOLUTION",
                        "원본 이미지 해상도가 카드 표시 크기에 부족합니다. "
                        "더 큰 공식 원본을 수집하세요.",
                        card.page,
                        "image_curator",
                    )
                )
        if (
            image.visible_text
            and any(
                language.lower() not in {"ko", "korean", "한국어"}
                for language in image.detected_languages
            )
            and not card.image_explanation.strip()
        ):
            issues.append(
                _issue(
                    "FOREIGN_IMAGE_UNEXPLAINED",
                    "이미지 속 외국어 핵심 내용을 한국어로 설명해야 합니다.",
                    card.page,
                    "copy_writer",
                )
            )
        if (
            image.visual_match == "exact"
            and image.related_fact_ids
            and not set(card.fact_ids).intersection(image.related_fact_ids)
        ):
            issues.append(
                _issue(
                    "IMAGE_FACT_MISMATCH",
                    "이미지와 카드가 설명하는 사실이 연결되지 않습니다.",
                    card.page,
                    "image_curator",
                )
            )
        if not image.official:
            issues.append(
                _issue(
                    "IMAGE_NOT_OFFICIAL",
                    "공식 이미지 여부가 확인되지 않았습니다.",
                    card.page,
                    "image_curator",
                )
            )
        if image.download_status == "failed":
            issues.append(
                _issue(
                    "IMAGE_DOWNLOAD_FAILED",
                    image.download_error or "이미지 파일을 내려받지 못했습니다.",
                    card.page,
                    "image_curator",
                )
            )
        if not image.eligible_for_editorial_use(
            package.sources,
            allow_risk_accepted=POLICY.allow_public_official_image_risk,
        ):
            issues.append(
                _issue(
                    "IMAGE_RIGHTS_REVIEW",
                    "공식 공개 출처 또는 사용 범위 근거가 확인되지 않았습니다.",
                    card.page,
                    "image_curator",
                )
            )
        key = image_visual_key(image)
        used.append(key)
        if image.used_in_previous_content or key in package.prior_image_fingerprints:
            issues.append(
                _issue(
                    "IMAGE_USED_BEFORE",
                    "이전 콘텐츠에서 사용한 이미지입니다.",
                    card.page,
                    "image_curator",
                )
            )
        if image.has_alpha and image.subject_box is None:
            issues.append(
                _issue(
                    "SUBJECT_BOX_MISSING",
                    "투명 이미지의 피사체 안전 영역이 계산되지 않았습니다.",
                    card.page,
                    "image_curator",
                )
            )

    coverage_issue = image_coverage_issue(package, card_count=len(package.cards))
    if coverage_issue:
        issues.append(QualityIssue.model_validate(coverage_issue))

    counts = Counter(used)
    seen_images: set[str] = set()
    for card in sorted(package.cards, key=lambda item: item.page):
        image = images.get(card.image_id)
        if not image:
            continue
        key = image.fingerprint or image.id
        is_reuse = key in seen_images
        seen_images.add(key)
        if counts[key] > 1 and is_reuse:
            issues.append(
                _issue(
                    "IMAGE_REUSED_RAW",
                    "같은 원본 이미지를 여러 카드의 주 이미지로 반복했습니다. "
                    "다른 공식 이미지를 수집해야 합니다.",
                    card.page,
                    "image_curator",
                )
            )
    return issues


def check_copy(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if not package.content_type:
        issues.append(
            _issue(
                "CONTENT_TYPE_MISSING",
                "추천 알고리즘용 대표 콘텐츠 유형을 지정해야 합니다.",
                owner="copy_writer",
            )
        )
    if not POLICY.min_cards <= len(package.cards) <= POLICY.max_cards:
        issues.append(
            _issue("CARD_COUNT", f"카드는 {POLICY.min_cards}~{POLICY.max_cards}장이어야 합니다.")
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
                    "copy_writer",
                )
            )
        if last.role != "ending":
            issues.append(
                _issue(
                    "ENDING_ROLE_MISSING",
                    "마지막 장은 앞선 소재를 회수하는 ending 역할이어야 합니다.",
                    last.page,
                    "copy_writer",
                )
            )

    fact_ids = {fact.id for fact in package.facts}
    for card in package.cards:
        title = card.plain_title
        body = " ".join(card.body_sentences).strip()
        text = f"{card.eyebrow} {title} {body} {card.image_explanation}".strip()
        if card.page == 1:
            for term in package.term_glossary:
                if term.category == "work_title" and term.korean not in text:
                    issues.append(
                        _issue(
                            "WORK_TITLE_NOT_KOREAN",
                            f"첫 장에 작품의 한국어 제목이 없습니다: {term.korean}",
                            card.page,
                            "copy_writer",
                        )
                    )
        for term in package.term_glossary:
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
                        "copy_writer",
                    )
                )
        glossary_japanese_runs = {
            run for term in package.term_glossary for run in re.findall(r"[ぁ-ヿ]+", term.original)
        }
        for match in re.finditer(r"[ぁ-ヿ]+", text):
            japanese_run = match.group()
            if japanese_run in glossary_japanese_runs:
                continue
            issues.append(
                _issue(
                    "JAPANESE_COPY_UNTRANSLATED",
                    "일본어 표기를 설명 없이 사용했습니다. 한국어 번역을 추가하거나 "
                    "원문 병기를 정리하세요: "
                    f"{japanese_run}",
                    card.page,
                    "copy_writer",
                )
            )
        if not title and not body:
            issues.append(
                _issue(
                    "IMAGE_ONLY_PAGE",
                    "텍스트 없이 이미지만 있는 페이지입니다.",
                    card.page,
                    "copy_writer",
                )
            )
        if len(title) > POLICY.max_title_chars:
            issues.append(
                _issue(
                    "TITLE_TOO_LONG",
                    "제목이 너무 길어 어절 단절 위험이 있습니다.",
                    card.page,
                    "copy_writer",
                )
            )
        if len(body) > POLICY.max_body_chars:
            issues.append(
                _issue(
                    "BODY_TOO_LONG",
                    "본문이 카드 한 장의 정보량을 초과했습니다.",
                    card.page,
                    "copy_writer",
                )
            )
        if any(term in text for term in AGENT_MONOLOGUE_TERMS):
            issues.append(
                _issue(
                    "AGENT_MONOLOGUE",
                    "독자에게 필요 없는 에이전트 혼잣말이 포함됐습니다.",
                    card.page,
                    "copy_writer",
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
                    "copy_writer",
                )
            )
        missing_facts = sorted(set(card.fact_ids) - fact_ids)
        if missing_facts:
            issues.append(
                _issue(
                    "COPY_UNGROUNDED",
                    f"근거 없는 fact_id가 있습니다: {missing_facts}",
                    card.page,
                    "fact_checker",
                )
            )
        for phrase in card.protected_phrases:
            if phrase not in text:
                issues.append(
                    _issue(
                        "PROTECTED_PHRASE_LOST",
                        f"보호 구절이 본문에 없습니다: {phrase}",
                        card.page,
                        "copy_writer",
                    )
                )
            elif not any(phrase in line for line in card.title_lines) and phrase in title:
                issues.append(
                    _issue(
                        "PROTECTED_PHRASE_SPLIT",
                        f"보호 구절이 제목 줄 사이에서 갈라졌습니다: {phrase}",
                        card.page,
                        "copy_writer",
                    )
                )

    if package.cards:
        fact_signatures = [tuple(sorted(card.fact_ids)) for card in package.cards]
        seen_signatures: set[tuple[str, ...]] = set()
        for card, signature in zip(package.cards, fact_signatures, strict=True):
            if signature and signature in seen_signatures:
                issues.append(
                    _issue(
                        "CARD_REDUNDANT",
                        "앞 카드와 근거 사실이 중복됩니다.",
                        card.page,
                        "copy_writer",
                    )
                )
            seen_signatures.add(signature)
        ending = package.cards[-1]
        ending_text = f"{ending.plain_title} {' '.join(ending.body_sentences)}".strip()
        if ending.ending_joke:
            if len(ending_text) > POLICY.max_ending_chars:
                issues.append(
                    _issue(
                        "ENDING_TOO_LONG",
                        "마지막 드립은 두 문장 이내로 줄여야 합니다.",
                        ending.page,
                        "copy_writer",
                    )
                )
            if any(term in ending_text for term in GENERIC_HUMOR_TERMS):
                issues.append(
                    _issue(
                        "ENDING_GENERIC",
                        "작품명만 바꿔도 성립하는 상투적 드립입니다.",
                        ending.page,
                        "copy_writer",
                    )
                )
            if any(mark in ending_text for mark in EXCESSIVE_PUNCTUATION):
                issues.append(
                    _issue(
                        "ENDING_LOUD",
                        "데드팬 결말에 과한 감탄·웃음 표시가 있습니다.",
                        ending.page,
                        "copy_writer",
                    )
                )
    return issues


def check_layout(package: ContentPackage) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for card in package.cards:
        layout = card.layout
        image = next((item for item in package.images if item.id == card.image_id), None)
        boxes = (layout.image_box, layout.copy_box)
        if any(
            box.x < 0 or box.y < 0 or box.x + box.width > 1.001 or box.bottom > 1.001
            for box in boxes
        ):
            issues.append(
                _issue(
                    "BOX_OVERFLOW",
                    "이미지 또는 글자 영역이 카드 밖으로 나갑니다.",
                    card.page,
                    "layout_director",
                )
            )
        if layout.template not in {"full_bleed", "topwide", "ending"} and (
            layout.image_box.x < 0.03
            or layout.image_box.x + layout.image_box.width > 0.97
            or layout.image_box.y < 0.03
            or layout.image_box.bottom > 0.97
        ):
            issues.append(
                _issue(
                    "EDGE_ATTACHED_IMAGE",
                    "일반 카드의 이미지가 화면 가장자리에 너무 붙어 있습니다.",
                    card.page,
                    "layout_director",
                )
            )
        gap = layout.copy_box.y - layout.image_box.bottom
        if card.role == "cover" and gap > POLICY.max_cover_vertical_gap:
            issues.append(
                _issue(
                    "COVER_EMPTY_GAP",
                    f"표지 이미지와 제목 사이 여백이 큽니다({gap:.2f}).",
                    card.page,
                    "layout_director",
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
                    "layout_director",
                )
            )
        if layout.contrast_mode not in {"dark_on_light", "light_on_dark", "scrim_required"}:
            issues.append(
                _issue(
                    "CONTRAST_UNKNOWN",
                    "텍스트 대비 방식이 지정되지 않았습니다.",
                    card.page,
                    "layout_director",
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
                        "layout_director",
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
    ]


SCORE_THRESHOLDS = {"pass": 85, "card_minimum": 75, "safety_minimum_per_card": 4}

FACT_BLOCKERS = {
    "SOURCE_NO_OFFICIAL",
    "SOURCE_NOT_HTTPS",
    "FACT_SOURCE_MISSING",
    "FACT_UNVERIFIED",
    "CAMPAIGN_DETAILS_MISSING",
    "COPY_UNGROUNDED",
}
IMAGE_BLOCKERS = {
    "IMAGE_MISSING",
    "IMAGE_LOW_RESOLUTION",
    "IMAGE_NOT_OFFICIAL",
    "IMAGE_DOWNLOAD_FAILED",
    "IMAGE_FACT_MISMATCH",
    "IMAGE_USED_BEFORE",
    "SUBJECT_BOX_MISSING",
    "IMAGE_RIGHTS_REVIEW",
    "IMAGE_COVERAGE_INSUFFICIENT",
    "IMAGE_REUSED_RAW",
}
LAYOUT_BLOCKERS = {
    "BOX_OVERFLOW",
    "IMAGE_CROP_RISK",
    "SUBJECT_CLIPPED",
    "IMAGE_ONLY_PAGE",
    "CARD_COUNT",
}
STORY_BLOCKERS = {
    "COVER_ROLE_MISSING",
    "ENDING_ROLE_MISSING",
    "CONTENT_TYPE_MISSING",
    "CONTENT_TYPE_MISMATCH",
}
SAFETY_BLOCKERS = {"SAFETY_HARMFUL_CONTENT"}

ISSUE_PENALTIES = {
    "EDGE_ATTACHED_IMAGE": 5,
    "COVER_EMPTY_GAP": 4,
    "CONTRAST_UNKNOWN": 6,
    "FOREIGN_IMAGE_UNEXPLAINED": 8,
    "TITLE_TOO_LONG": 6,
    "BODY_TOO_LONG": 6,
    "AGENT_MONOLOGUE": 5,
    "RESEARCH_DELEGATED_TO_USER": 7,
    "PROTECTED_PHRASE_LOST": 8,
    "PROTECTED_PHRASE_SPLIT": 8,
    "CARD_REDUNDANT": 6,
    "ENDING_TOO_LONG": 4,
    "ENDING_GENERIC": 6,
    "ENDING_LOUD": 3,
}


def score_quality(
    package: ContentPackage,
    issues: list[QualityIssue],
    *,
    language_score: int | None = None,
    safety_score: int | None = None,
    card_language_scores: dict[int, int] | None = None,
) -> dict[str, object]:
    """Score deterministic checks and a separate reviewer language assessment."""
    fact_issues = [issue for issue in issues if issue.code in FACT_BLOCKERS]
    image_issues = [issue for issue in issues if issue.code in IMAGE_BLOCKERS]
    layout_issues = [
        issue
        for issue in issues
        if issue.owner == "layout_director" or issue.code in LAYOUT_BLOCKERS
    ]
    copy_issues = [issue for issue in issues if issue.owner == "copy_writer"]

    evidence_score = 25 if not fact_issues else 0
    used_image_ids = {card.image_id for card in package.cards}
    risk_accepted_count = sum(
        image.id in used_image_ids and image.rights_status.value == "official_public_risk_accepted"
        for image in package.images
    )
    image_score = 0 if image_issues else (12 if risk_accepted_count else 15)
    crop_codes = {"IMAGE_CROP_RISK", "SUBJECT_CLIPPED"}
    image_visibility_score = 0 if any(issue.code in crop_codes for issue in issues) else 10
    if any(issue.code in LAYOUT_BLOCKERS for issue in issues):
        readability_score = 0
    else:
        readability_penalty = 6 * len(layout_issues) + sum(
            ISSUE_PENALTIES.get(issue.code, 4)
            for issue in copy_issues
            if issue.code
            in {
                "FOREIGN_IMAGE_UNEXPLAINED",
                "TITLE_TOO_LONG",
                "BODY_TOO_LONG",
                "PROTECTED_PHRASE_LOST",
                "PROTECTED_PHRASE_SPLIT",
            }
        )
        readability_score = max(0, 15 - readability_penalty)
    editorial_score = max(
        0,
        5
        - sum(
            ISSUE_PENALTIES.get(issue.code, 0)
            for issue in issues
            if issue.code
            in {
                "AGENT_MONOLOGUE",
                "RESEARCH_DELEGATED_TO_USER",
                "CARD_REDUNDANT",
                "ENDING_TOO_LONG",
                "ENDING_GENERIC",
                "ENDING_LOUD",
            }
        ),
    )
    scores = {
        "evidence_and_accuracy": evidence_score,
        "image_suitability_and_rights": image_score,
        "image_full_visibility": image_visibility_score,
        "readability_and_layout": readability_score,
        "korean_naturalness_and_translation": language_score or 0,
        "structure_and_style_rules": editorial_score,
        "safety_and_respect": safety_score or 0,
    }
    total = sum(scores.values())

    card_scores: dict[str, int] = {}
    for card in package.cards:
        page_issues = [issue for issue in issues if issue.page == card.page]
        penalty = sum(ISSUE_PENALTIES.get(issue.code, 12) for issue in page_issues)
        blocking_codes = IMAGE_BLOCKERS | LAYOUT_BLOCKERS | STORY_BLOCKERS
        if any(issue.code in blocking_codes for issue in page_issues):
            penalty = max(penalty, 30)
        rule_score = max(0, 100 - penalty)
        language_card_score = (card_language_scores or {}).get(card.page)
        card_scores[str(card.page)] = min(
            rule_score,
            language_card_score if language_card_score is not None else rule_score,
        )

    codes = {issue.code for issue in issues}
    hard_blockers = sorted(
        codes
        & (FACT_BLOCKERS | IMAGE_BLOCKERS | LAYOUT_BLOCKERS | STORY_BLOCKERS | SAFETY_BLOCKERS)
    )
    lowest_card = min(card_scores.values(), default=100)
    if any(issue.code in FACT_BLOCKERS for issue in issues):
        decision = "return_to_crawler"
    elif any(issue.code in IMAGE_BLOCKERS for issue in issues):
        decision = "return_to_crawler"
    elif language_score is None and not hard_blockers:
        decision = "pending_language_review"
    elif (
        hard_blockers
        or total < SCORE_THRESHOLDS["pass"]
        or lowest_card < SCORE_THRESHOLDS["card_minimum"]
    ):
        decision = "revise"
    elif issues:
        decision = "revise"
    else:
        decision = "pass_candidate"

    return {
        "total": total,
        "maximum": 100,
        "thresholds": SCORE_THRESHOLDS,
        "dimensions": scores,
        "accepted_image_risk_count": risk_accepted_count,
        "accepted_image_risk_note": (
            "공식 공개 이미지의 출처를 확인했으나 사용 허가를 확인한 것은 아닙니다."
            if risk_accepted_count
            else ""
        ),
        "card_scores": card_scores,
        "lowest_card_score": lowest_card,
        "hard_blockers": hard_blockers,
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
