"""Render structured card manifests as a human-readable Markdown handoff."""

from __future__ import annotations

from ani_menu_agent.models import ContentPackage


def render_markdown(package: ContentPackage) -> str:
    """Export cards without making Markdown the canonical storage format."""
    lines = [
        f"# {package.topic}",
        "",
        f"- 콘텐츠 유형: {package.content_kind.value}",
        f"- 추천 콘텐츠 타입: {package.content_type or '미분류'}",
        f"- 상태: {package.status}",
        f"- 지역 우선순위: {package.region_policy}",
        "",
    ]

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

    return "\n".join(lines).rstrip() + "\n"
