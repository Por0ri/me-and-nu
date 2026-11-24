"""Evidence required to reopen a returned news, event, or goods request."""

from ani_menu_agent.image_selection import (
    InsufficientCardImages,
    assign_distinct_images,
    copy_image_pool,
    distinct_editorial_images,
    image_visual_keys,
)
from ani_menu_agent.models import ContentPackage, CopyCard
from ani_menu_agent.period_copy import episode_evidence_missing, reported_terms
from ani_menu_agent.period_schedule import primary_source_schedule
from ani_menu_agent.policy import POLICY
from ani_menu_agent.source_freshness import (
    expiry_evidence,
    immediate_news_action_expiry,
    publication_timestamp,
)

IMAGE_ISSUES = {
    "COVER_SOURCE_IMAGE_MISSING",
    "SOURCE_IMAGE_MISSING",
    "IMAGE_MISSING",
    "IMAGE_DOWNLOAD_FAILED",
    "SOURCE_IMAGE_POOL_INSUFFICIENT",
    "IMAGE_REUSED_RAW",
}
DATE_ISSUES = {"EVENT_PERIOD_MISSING", "GOODS_RELEASE_PERIOD_MISSING", "SOURCE_DATE_UNRESOLVED"}
REPAIRABLE_ISSUES = IMAGE_ISSUES | DATE_ISSUES | {"EPISODE_NOTICE_EVIDENCE_MISSING"}


def evidence_snapshot(package: ContentPackage) -> dict:
    """Remember usable visuals, including CDN aliases, and dates from the source article."""
    images = distinct_editorial_images(package.images, package.sources, package)
    keys = {key for image in images for key in image_visual_keys(image)}
    dates = primary_source_schedule(package, package.content_type)
    if package.content_type == "news" and package.sources:
        published = publication_timestamp(package.sources[0].published_at)
        if published:
            dates["published_at"] = published[:10]
    snapshot = {"visual_keys": sorted(keys), "dates": dates}
    if package.content_type == "news":
        snapshot["episode_claims"] = [
            term["excerpt"] for term in reported_terms(package) if term["term"] in {"episode_release", "episode_edition"}
        ]
    return snapshot


def has_repaired_evidence(package: ContentPackage, baseline: dict, issues: list[dict]) -> bool:
    codes = {issue.get("code") for issue in issues}
    if not codes.intersection(REPAIRABLE_ISSUES):
        return False
    data = package.model_dump(mode="json")
    if package.content_type == "news":
        if episode_evidence_missing(package) or immediate_news_action_expiry(data)["status"] in {"expired", "unresolved"}:
            return False
    elif expiry_evidence(data, package.content_type)["status"] != "active":
        return False
    if "EPISODE_NOTICE_EVIDENCE_MISSING" in codes:
        claims = set(evidence_snapshot(package).get("episode_claims", []))
        if episode_evidence_missing(package) or not claims.difference(baseline.get("episode_claims", [])):
            return False
    if codes.intersection(DATE_ISSUES):
        current = evidence_snapshot(package)["dates"]
        old = baseline["dates"]
        if not any(
            key not in old or (value.startswith(old[key]) and len(value) > len(old[key]))
            for key, value in current.items()
        ):
            return False
    image_issues = [issue for issue in issues if issue.get("code") in IMAGE_ISSUES]
    pages = [int(issue.get("page") or 0) for issue in image_issues]
    required = max([POLICY.minimum_cards_for(package.content_type), *pages])
    try:
        selected = copy_image_pool(package, limit=required)
    except InsufficientCardImages:
        return False
    if len(selected) < required:
        return False
    if image_issues:
        old_keys = set(baseline["visual_keys"])
        eligible = distinct_editorial_images(package.images, package.sources, package)
        new_images = [
            im
            for im in eligible
            if (im.fingerprint or im.storage_key) and image_visual_keys(im).isdisjoint(old_keys)
        ]
        if not new_images:
            return False
        if any(
            issue.get("code") == "COVER_SOURCE_IMAGE_MISSING"
            or (issue.get("code") in IMAGE_ISSUES and issue.get("page") == 1)
            for issue in issues
        ):
            cover = CopyCard(page=1, role="cover", title_lines=[""], image_id="")
            [assigned] = assign_distinct_images(
                [cover], package.model_copy(update={"images": new_images})
            )
            if assigned.image_reuse_mode == "missing_source_image":
                return False
        return any(image_visual_keys(im).isdisjoint(old_keys) for im in selected)
    return True
