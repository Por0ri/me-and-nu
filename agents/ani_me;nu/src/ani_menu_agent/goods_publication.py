"""Reader-facing labels and copy checks for artwork used to illustrate goods news."""

import re

from ani_menu_agent.image_selection import goods_reference_image_label
from ani_menu_agent.models import ContentPackage, QualityIssue
from ani_menu_agent.review_rules import make_issue
from ani_menu_agent.sources import period_images


def goods_reference_labels(package: ContentPackage) -> dict[int, str]:
    if package.content_type != "goods":
        return {}
    images = {image.id: image for image in package.images}
    labels = {}
    for card in package.cards:
        image = images.get(card.image_id)
        if image and (label := goods_reference_image_label(image, package)):
            labels[card.page] = label
    return labels


def set_goods_image_labels(package: ContentPackage) -> None:
    labels = goods_reference_labels(package)
    for card in package.cards:
        card.image_label = labels.get(card.page, "")


def illustrated_product_url(package: ContentPackage) -> str | None:
    return (
        period_images.verified_goods_product_url(package)
        if goods_reference_labels(package)
        else None
    )


def reader_detail_body(package: ContentPackage, *, markdown: bool) -> str:
    url = illustrated_product_url(package)
    if url and package.detail_body.strip() and url not in package.detail_body:
        label = "더 궁금하면 제조사 상품 페이지를 봐!"
        link = f"[{label}]({url})" if markdown else f"{label}\n{url}"
        return f"{package.detail_body.rstrip()}\n\n{link}"
    return package.detail_body


# Only explicit claims about what the displayed picture shows. An article may still
# describe a genuine product feature or tell the reader where product photos are.
_PRESENTED_AS_PRODUCT = re.compile(
    r"(?:이|지금\s*보이는|위|아래|표지의?)\s*(?:사진|이미지|일러스트|그림|포스터)(?:이|가|은|는|야|에\s*보이는\s*건)"
    r"\s*(?:바로\s*)?(?:실물(?:\s*(?:컷|사진))?|(?:실제\s*)?(?:제품|피규어)\s*(?:컷|사진|실물))"
    r"(?:이야|이래|야|이다|이에요|입니다|!)"
    r"|(?:이|사진\s*속|그림\s*속|일러스트\s*속)\s*(?:포즈|의상|복장)(?:가|이|를|을)?\s*그대로"
    r"\s*(?:피규어|제품|굿즈)(?:로|에)\s*(?:나왔|옮겨|재현|구현)"
)


def goods_illustration_copy_issues(package: ContentPackage) -> list[QualityIssue]:
    pages = goods_reference_labels(package)
    if not pages:
        return []
    sections = [
        (card.page, " ".join([card.eyebrow, card.plain_title, *card.body_sentences]))
        for card in package.cards
        if card.page in pages
    ]
    if len(pages) == len(package.cards):
        sections.append((None, package.detail_body))
    return [
        make_issue(
            code="COPY_UNGROUNDED" if page else "DETAIL_BODY_FACTUAL_SUPPORT",
            page=page,
            message=(
                "작품 이미지를 제품 실물로 소개한 문구를 고쳐 주세요. "
                "제품 특징은 상품 원문으로 설명하세요."
            ),
        )
        for page, text in sections
        if _PRESENTED_AS_PRODUCT.search(text)
    ]
