from ani_menu_agent.image_selection import cover_usable_editorial_images
from ani_menu_agent.models import ImageAsset, RightsStatus, SourceEvidence


def test_sparse_copy_prompt_uses_only_cover_resolution_images() -> None:
    source = SourceEvidence(
        id="official",
        title="Official event",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )

    def image(image_id: str, width: int, height: int) -> ImageAsset:
        return ImageAsset(
            id=image_id,
            url=f"https://example.com/images/{image_id}.jpg",
            storage_key=image_id,
            source_url=source.url,
            official=True,
            rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
            width=width,
            height=height,
            rights_evidence_url=source.url,
            rights_checked_at="2026-10-21T00:00:00Z",
            attribution_text="Official source image",
            download_status="downloaded",
        )

    images = [image("hero", 1200, 630), image("small", 786, 393)]

    usable = cover_usable_editorial_images(images, [source])

    assert [item.id for item in usable] == ["hero"]
