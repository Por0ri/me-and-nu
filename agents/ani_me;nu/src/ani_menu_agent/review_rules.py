"""Canonical review rules and routing metadata shared by generation and QA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ani_menu_agent.models import QualityIssue
from ani_menu_agent.policy import POLICY

ReviewCheck = Literal["code", "llm", "both"]
ReviewSeverity = Literal["blocker", "revise", "warning"]
ReviewOwner = Literal[
    "copy_writer", "detail_writer", "image_curator", "layout_director", "crawler"
]
ReviewFailureRoute = Literal["revise_owner", "return_to_crawler"]


@dataclass(frozen=True)
class ReviewRule:
    code: str
    rule: str
    check: ReviewCheck
    severity: ReviewSeverity
    owner: ReviewOwner
    on_fail: ReviewFailureRoute
    applies_to: tuple[str, ...] = ()


_RULES: dict[str, ReviewRule] = {}


def _register(
    codes: tuple[str, ...],
    rule: str,
    *,
    check: ReviewCheck = "code",
    severity: ReviewSeverity = "revise",
    owner: ReviewOwner = "copy_writer",
    on_fail: ReviewFailureRoute = "revise_owner",
    applies_to: tuple[str, ...] = (),
) -> None:
    for code in codes:
        if code in _RULES:
            raise ValueError(f"Review rule registered more than once: {code}")
        _RULES[code] = ReviewRule(code, rule, check, severity, owner, on_fail, applies_to)


# Evidence, attribution, rights, and safety are non-negotiable publication gates.
_register(
    (
        "SOURCE_NOT_HTTPS", "FACT_SOURCE_MISSING", "FACT_UNVERIFIED",
        "EVENT_ALREADY_ENDED", "COPY_UNGROUNDED", "SOURCE_STALE",
        "EVENT_PUBLICATION_DATE_SHOWN", "UNSUPPORTED_DATE_CLAIM",
        "SOURCE_PUBLICATION_DATE_SHOWN", "SOURCE_APPROVAL_REQUIRED",
        "SOURCE_BODY_EMPTY", "CONTENT_TYPE_SOURCE_UNSUPPORTED",
    ),
    (
        'Every factual claim must be supported by the recorded source and linked to the '
        'correct work; do not invent or silently alter facts.'
    ),
    check="both", severity="blocker", owner="crawler", on_fail="return_to_crawler",
)
_register(
    ("SOURCE_NO_OFFICIAL",),
    (
        'An official first-party source is not required when the facts use an active, '
        'approved source registered for this content type.'
    ),
    check="code", severity="warning", owner="crawler",
)
_register(
    ("FACT_DIVERSITY_LOW",),
    "Use the available verified facts without requiring an arbitrary number of separate claims.",
    check="code", severity="warning", owner="detail_writer",
)
_register(
    (
        "IMAGE_RIGHTS_REVIEW", "IMAGE_NOT_OFFICIAL", "IMAGE_SOURCE_PAGE_MISMATCH",
        "IMAGE_FACT_MISMATCH",
    ),
    (
        'Use an image only when its source and work relationship are recorded and its use '
        'passes the configured rights and attribution checks.'
    ),
    check="both", severity="blocker", owner="image_curator",
)
_register(
    ("SAFETY_HARMFUL_CONTENT", "SAFETY_RESPECT_LOW"),
    (
        'Block harmful, hateful, discriminatory, sexualized-minor, or seriously '
        'disrespectful material; this safety gate is not waived by a high total score.'
    ),
    check="llm", severity="blocker", owner="copy_writer",
)
_register(
    ("CAMPAIGN_DETAILS_MISSING", "EVENT_PERIOD_MISSING", "GOODS_RELEASE_PERIOD_MISSING"),
    (
        'Do not publish time-sensitive event or sales content when its essential dates or '
        'participation conditions cannot be verified from a source.'
    ),
    check="both", severity="blocker", owner="crawler", on_fail="return_to_crawler",
    applies_to=("event", "goods"),
)
_register(
    ("MULTIPLE_GOODS_RELEASE_DATES",),
    (
        'Preserve distinct verified release dates for different products and explain which '
        'date belongs to each item.'
    ),
    check="code", severity="blocker", owner="detail_writer", applies_to=("goods",),
)
_register(
    ("CARD_COUNT",),
    (
        f"A carousel must contain at least {POLICY.min_cards} cards and "
        f"no more than {POLICY.max_cards} cards."
    ),
    check="code", severity="blocker", owner="copy_writer",
)

# Collection failures return to collection; they are not treated as copy defects.
_register(
    ("INVALID_INPUT",),
    "The source record must be valid and complete enough to enter the content pipeline.",
    check="code", severity="blocker", owner="crawler", on_fail="return_to_crawler",
)
_register(
    (
        "COVER_SOURCE_IMAGE_MISSING", "SOURCE_IMAGE_MISSING", "IMAGE_MISSING",
        "IMAGE_DOWNLOAD_FAILED", "SOURCE_IMAGE_POOL_INSUFFICIENT",
    ),
    (
        'Provide a usable, work-relevant source image for each image-led card; if the pool '
        'is short, collect a suitable image before retrying.'
    ),
    check="code", severity="blocker", owner="crawler", on_fail="return_to_crawler",
)
_register(
    ("WORK_ID_UNRESOLVED",),
    (
        'Resolve the representative work ID against the canonical work registry, creating a '
        'new registry entry when the work is genuinely new.'
    ),
    check="both", severity="blocker", owner="crawler", on_fail="return_to_crawler",
)

# Image suitability and composition should be repaired with a better asset or layout.
_register(
    (
        "IMAGE_VISION_MISMATCH", "IMAGE_COMPOSITION_REJECTED", "IMAGE_CROP_RISK",
        "SUBJECT_CLIPPED",
    ),
    (
        'When the reviewer rates image relevance or composition below the configured '
        'minimum, or detects a materially clipped subject, replace or repair the image '
        'before approval.'
    ),
    check="both", severity="blocker", owner="image_curator",
)
_register(
    (
        "IMAGE_LOW_RESOLUTION", "IMAGE_USED_BEFORE", "SUBJECT_BOX_MISSING",
        "IMAGE_ASSET_NOT_CARDWORTHY", "IMAGE_REUSED_RAW", "IMAGE_FACT_LINK_MISSING",
        "FOREIGN_IMAGE_UNEXPLAINED", "IMAGE_COPY_REGION_OVERLAP", "BOX_OVERFLOW",
        "TEXT_OVERLAY_SAFE_AREA_INVALID", "IMAGE_ONLY_PAGE", "COVER_EMPTY_GAP",
        "EDGE_ATTACHED_IMAGE", "CONTRAST_UNKNOWN",
    ),
    (
        'Accept natural whitespace caused by the source image ratio and the HTML copy area; '
        'when an image or layout is genuinely unusable, replace the asset or adjust its '
        'layout without asking the reviewer to crop or enlarge it.'
    ),
    check="both", severity="revise", owner="image_curator",
)
_register(
    ("IMAGE_RENDER_MISSING", "IMAGE_VISION_REVIEW_MISSING"),
    (
        'Every card image must render and receive the required visual review before a '
        'candidate can pass.'
    ),
    check="both", severity="blocker", owner="image_curator",
)

# Copy and detail quality use the same lower configured score floor.
_register(
    (
        "TITLE_TOO_LONG", "BODY_TOO_LONG", "AGENT_MONOLOGUE", "RESEARCH_DELEGATED_TO_USER",
        "PROTECTED_PHRASE_LOST", "PROTECTED_PHRASE_SPLIT", "JAPANESE_COPY_UNTRANSLATED",
        "JAPANESE_TERM_MISTRANSLATED", "JAPANESE_TERM_UNPAIRED", "WORK_TITLE_NOT_KOREAN",
        "CARD_REDUNDANT", "ENDING_GENERIC", "ENDING_LOUD", "EVENT_SCHEDULE_IN_TITLE",
    ),
    (
        'Polish copy for clear, natural Korean and a specific fan-oriented voice while '
        'preserving source facts, names, dates, and prices.'
    ),
    check="both", severity="revise", owner="copy_writer",
)
_register(
    (
        "CONTENT_TYPE_MISSING", "CONTENT_TYPE_MISMATCH", "COVER_ROLE_MISSING",
        "ENDING_ROLE_MISSING", "CARD_FACT_REUSED", "CAMPAIGN_EXPIRED",
        "ENDING_TOO_LONG", "QUALITY_SCORE_BELOW_THRESHOLD",
    ),
    (
        'Keep the card sequence coherent for its assigned content type, with a clear cover, '
        'distinct information cards, and a purposeful ending.'
    ),
    check="both", severity="revise", owner="copy_writer",
)
_register(
    (
        "DETAIL_BODY_MISSING", "DETAIL_BODY_REVIEW_MISSING", "DETAIL_BODY_LLM_REQUIRED",
        "DETAIL_BODY_OUTPUT_INVALID", "DETAIL_BODY_LENGTH", "DETAIL_BODY_FACT_LINK_MISSING",
        "DETAIL_BODY_FACT_UNVERIFIED", "DETAIL_BODY_SOURCE_MISSING",
        "DETAIL_BODY_SOURCE_LINK_MISSING",
    ),
    (
        'The detail article must be readable, source-linked, and supported by verified '
        'facts; include all available essential event or product conditions without '
        'inventing missing information.'
    ),
    check="both", severity="blocker", owner="detail_writer",
)
_register(
    ("DETAIL_BODY_COMPLETENESS",),
    (
        'For event, campaign, and sales articles, include each available verified date, '
        'location, price, participation method, prize, deadline, and eligible region; do '
        'not invent unavailable conditions.'
    ),
    check="llm", severity="revise", owner="detail_writer", applies_to=("event", "goods"),
)
_register(
    (
        "DETAIL_BODY_NATURALNESS", "DETAIL_BODY_FACTUAL_SUPPORT", "DETAIL_BODY_COHERENCE",
    ),
    (
        'The detail article should read naturally, follow a clear paragraph flow, and make '
        'no claim beyond the linked facts.'
    ),
    check="llm", severity="revise", owner="detail_writer",
)
_register(
    (
        "LANGUAGE_KOREAN_NATURALNESS", "LANGUAGE_TRANSLATION_NATURALNESS",
        "LANGUAGE_READER_CLARITY", "LANGUAGE_FAN_ENGAGEMENT", "LANGUAGE_CARD_FLOW",
    ),
    (
        'A language rating below the configured minimum creates a revise item and its score '
        'deduction; it does not independently reject content when the aggregate and '
        'per-card thresholds are met.'
    ),
    check="llm", severity="revise", owner="copy_writer",
)
_register(
    (
        "EVENT_LOCATION_MISSING", "EVENT_COUNTRY_MISSING", "EVENT_REGION_MISSING",
        "SINGLE_DAY_PERIOD_NOT_MIRRORED",
    ),
    (
        'Optional location, country, region, or normalized single-day metadata gaps may be '
        'logged without blocking otherwise supported content.'
    ),
    check="code", severity="warning", owner="crawler", on_fail="return_to_crawler",
    applies_to=("event", "goods"),
)
_register(
    ("REVIEW_OUTPUT_INVALID", "REVIEW_CARD_SET_MISMATCH"),
    (
        'An incomplete or malformed independent review cannot approve content; retry the '
        'reviewer and preserve its complete per-card result.'
    ),
    check="code", severity="blocker", owner="crawler", on_fail="return_to_crawler",
)

RULES: dict[str, ReviewRule] = dict(_RULES)

_SHARED_GUIDANCE: dict[str, tuple[str, ...]] = {
    "copy_writer": (
        (
            f"Keep each card body near {POLICY.max_body_chars} Korean characters and put "
            "the key point on the card; put supporting detail in the detail article.",
        ),
        (
            'List in protected_phrases only exact names, dates, prices, or other protected '
            'terms that actually appear on that card.'
        ),
        (
            "Do not put navigation copy such as 'see details' on a card. Keep HTML copy below "
            'the image on every page; never place it above or beside the artwork. PNG files '
            'contain no text.'
        ),
    ),
    "detail_writer": (
        (
            'Every fact ID used by a card must also appear in detail_fact_ids, and the detail '
            'article must explain verified evidence rather than repeat card copy.'
        ),
    ),
}


def get_rule(code: str) -> ReviewRule:
    try:
        return RULES[code]
    except KeyError as exc:
        raise ValueError(f"Unregistered review issue code: {code}") from exc


_SCORE_PENALTIES = dict(POLICY.review_issue_penalties)


def score_penalty(code: str) -> int:
    rule = get_rule(code)
    if rule.severity != "revise":
        return 0
    return _SCORE_PENALTIES.get(code, POLICY.default_review_issue_penalty)


def make_issue(*, code: str, message: str = "", page: int | None = None) -> QualityIssue:
    """Create an issue with owner and severity from the canonical registry."""
    rule = get_rule(code)
    return QualityIssue(
        code=code,
        message=message or rule.rule,
        page=page,
        owner=rule.owner,
        severity=rule.severity,
    )


def prompt_rules(*, check: ReviewCheck, owners: set[str] | None = None) -> str:
    selected = [
        rule for rule in RULES.values()
        if (check == "both" or rule.check in {check, "both"})
        and (owners is None or rule.owner in owners)
    ]
    statements = [rule.rule for rule in selected]
    for owner, guidance in _SHARED_GUIDANCE.items():
        if owners is None or owner in owners:
            statements.extend(guidance)
    return "\n".join(f"- {statement}" for statement in dict.fromkeys(statements))


def decision_metadata(issues: list[QualityIssue]) -> tuple[list[str], list[str], bool]:
    """Return blockers, revise issues, and whether any issue returns to collection."""
    blockers: list[str] = []
    actionable: list[str] = []
    return_to_crawler = False
    for issue in issues:
        rule = get_rule(issue.code)
        if rule.severity == "blocker":
            blockers.append(issue.code)
            return_to_crawler |= rule.on_fail == "return_to_crawler"
        elif rule.severity == "revise":
            actionable.append(issue.code)
            return_to_crawler |= rule.on_fail == "return_to_crawler"
    return sorted(set(blockers)), sorted(set(actionable)), return_to_crawler
