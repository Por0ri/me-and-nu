"""Render structured card manifests as a human-readable Markdown handoff."""

from __future__ import annotations

from ani_menu_agent.models import ContentPackage

AI_DISCLOSURE_TEXT = "이 콘텐츠는 원문과 확인된 자료를 바탕으로 AI를 활용해 생성·정리했습니다."


def render_detail_markdown(package: ContentPackage) -> str:
    """Build the complete reader-facing detail text with source credits and disclosure."""
    lines = [package.detail_body.strip(), "", "## 출처", "", "원문", ""]
    allowed_source_ids = set(package.detail_source_ids)
    seen_sources: set[str] = set()
    selected_sources = [
        source for source in package.sources
        if not allowed_source_ids or source.id in allowed_source_ids
    ]
    if not selected_sources:
        selected_sources = package.sources
    for source in selected_sources:
        url = str(source.url)
        if url in seen_sources:
            continue
        seen_sources.add(url)
        lines.append(f"- [{source.publisher} — {source.title}]({url})")
    if not seen_sources:
        lines.append("- 원문 출처 정보가 없습니다.")

    source_by_url = {str(source.url): source for source in package.sources}
    used_image_ids = {card.image_id for card in package.cards if card.image_id}
    used_images = [
        image for image in package.images
        if image.id in used_image_ids
    ]
    if not used_images and not package.cards:
        used_images = package.images
    lines.extend(["", "이미지", ""])
    seen_image_sources: set[str] = set()
    for image in used_images:
        image_source = image.provenance_source or source_by_url.get(str(image.source_url))
        url = str(image_source.url) if image_source else str(image.source_url)
        if url in seen_image_sources:
            continue
        seen_image_sources.add(url)
        if image_source:
            label = f"{image_source.publisher} — {image_source.title}"
        else:
            label = image.source_description.strip() or "이미지 원문"
        attribution = image.attribution_text.strip()
        suffix = f" — {attribution}" if attribution and attribution not in label else ""
        lines.append(f"- [{label}]({url}){suffix}")
    if not seen_image_sources:
        lines.append("- 사용 이미지의 별도 출처가 없습니다.")

    lines.extend(["", "## AI 활용 안내", "", AI_DISCLOSURE_TEXT, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(package: ContentPackage) -> str:
    """Export cards without making Markdown the canonical storage format."""
    lines = [
        f"# {package.topic}",
        "",
        f"- 콘텐츠 유형: {package.content_kind.value}",
        f"- 추천 콘텐츠 타입: {package.content_type or '미분류'}",
        f"- 상태: {package.status}",
    ]
    if package.event_country:
        lines.append(f"- 국가: {package.event_country}")
    if package.event_start and package.event_end:
        lines.append(f"- 행사/발매 기간: {package.event_start} ~ {package.event_end}")
    lines.append("")

    source_by_id = {source.id: source for source in package.sources}
    fact_by_id = {fact.id: fact for fact in package.facts}
    image_by_id = {image.id: image for image in package.images}

    for card in sorted(package.cards, key=lambda item: item.page):
        lines.extend([f"## {card.page:02d} · {card.role}", ""])
        if card.eyebrow:
            lines.extend([f"_{card.eyebrow}_", ""])
        lines.extend([f"### {card.plain_title}", ""])
        lines.extend(card.body_sentences)
        if card.body_sentences:
            lines.append("")
        if card.ending_joke:
            lines.extend(["_마지막 드립 카드_", ""])

        image = image_by_id.get(card.image_id)
        if image:
            image_location = image.local_path or str(image.url or "")
            lines.extend([f"이미지: `{image_location}`", ""])
            if card.image_explanation:
                lines.extend([f"> 이미지 속 정보: {card.image_explanation}", ""])

        related_facts = [fact_by_id[fact_id] for fact_id in card.fact_ids if fact_id in fact_by_id]
        if related_facts:
            lines.append("근거 사실:")
            for fact in related_facts:
                lines.append(f"- {fact.statement}")
                for source_id in fact.source_ids:
                    source = source_by_id.get(source_id)
                    if source:
                        lines.append(
                            f"  - 출처: [{source.publisher} — {source.title}]({source.url})"
                        )
            lines.append("")

    lines.extend(["## 자세히 보기", "", render_detail_markdown(package)])

    return "\n".join(lines).rstrip() + "\n"
