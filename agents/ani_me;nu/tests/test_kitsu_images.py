from __future__ import annotations

from typing import Any

from ani_menu_agent import cli
from ani_menu_agent.models import ContentKind, ContentPackage, ImageAsset, SourceEvidence
from ani_menu_agent.sources import kitsu_images
from ani_menu_agent.sources.models import Source


def test_kitsu_work_images_require_exact_catalog_title_and_keep_provenance(monkeypatch) -> None:
    source = Source(
        id="kitsu_anime_catalog",
        name="Kitsu Anime Catalog API",
        url="https://kitsu.io/api/edge/anime",
        category="kitsu_anime_catalog",
        role="reference",
        collection="api",
        status="active",
        region="global",
        allowed_asset_hosts=["media.kitsu.app"],
        min_interval_seconds=0,
    )
    monkeypatch.setattr(
        kitsu_images,
        "load_work_catalog",
        lambda: [
            {
                "menu_work_id": 17,
                "title_ko": "테스트 작품",
                "title_romaji": "Test Anime",
                "title_english": "Test Anime",
                "title_native": "テストアニメ",
            }
        ],
    )
    monkeypatch.setattr(kitsu_images, "pace_host", lambda *_args: None)

    class FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.content = b"{}"
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            if "filter%5Btext%5D" in url or "filter[text]" in url:
                return FakeResponse(
                    {
                        "data": [
                            {
                                "id": "999",
                                "attributes": {
                                    "canonicalTitle": "Unrelated Anime",
                                    "titles": {"en": "Unrelated Anime"},
                                    "posterImage": {
                                        "original": "https://media.kitsu.app/anime/999/poster.jpg"
                                    },
                                },
                            },
                            {
                                "id": "17",
                                "attributes": {
                                    "canonicalTitle": "Test Anime",
                                    "titles": {"en": "Test Anime"},
                                    "posterImage": {
                                        "original": "https://media.kitsu.app/anime/17/poster.jpg"
                                    },
                                "coverImage": {
                                    "original": "https://media.kitsu.app/anime/17/cover.jpg"
                                },
                                },
                            },
                        ]
                    }
                )
            if "/episodes?" in url:
                return FakeResponse(
                    {
                        "data": [
                            {
                                "attributes": {
                                    "canonicalTitle": "Episode One",
                                    "thumbnail": {
                                        "original": "https://media.kitsu.app/episodes/1.jpg"
                                    },
                                }
                            },
                            {
                                "attributes": {
                                    "canonicalTitle": "Episode Two",
                                    "thumbnail": {
                                        "original": "https://media.kitsu.app/episodes/2.jpg"
                                    },
                                }
                            },
                        ]
                    }
                )
            return FakeResponse(
                {
                    "data": [
                        {
                            "attributes": {"role": "main"},
                            "relationships": {"character": {"data": {"id": "c1"}}},
                        },
                        {
                            "attributes": {"role": "main"},
                            "relationships": {"character": {"data": {"id": "c2"}}},
                        },
                    ],
                    "included": [
                        {
                            "type": "characters",
                            "id": "c1",
                            "attributes": {
                                "canonicalName": "Hero",
                                "image": {
                                    "original": "https://media.kitsu.app/characters/1.jpg"
                                },
                            },
                        },
                        {
                            "type": "characters",
                            "id": "c2",
                            "attributes": {
                                "canonicalName": "Friend",
                                "image": {
                                    "original": "https://media.kitsu.app/characters/2.jpg"
                                },
                            },
                        },
                    ],
                }
            )

    monkeypatch.setattr(kitsu_images.httpx, "Client", FakeClient)
    assets = kitsu_images.collect_kitsu_work_images([17], source)

    assert len(assets) == 6
    assert {asset.asset_kind for asset in assets} == {
        "catalog_poster",
        "catalog_cover",
        "character_art",
        "scene_still",
    }
    assert all(asset.menu_work_ids == [17] for asset in assets)
    assert all(asset.provenance_source.id == "kitsu_anime_catalog" for asset in assets)
    assert all(asset.rights_status.value == "needs_review" for asset in assets)
    assert all(asset.visual_match == "context" for asset in assets)
    assert {asset.depicted_entities[0] for asset in assets if asset.depicted_entities} == {
        "Hero",
        "Friend",
    }


def test_generation_resolves_work_ids_before_kitsu_visual_fallback(monkeypatch) -> None:
    package = ContentPackage(
        request_id="kitsu-fallback-work-resolution",
        topic="Review",
        content_kind=ContentKind.INFORMATION,
        sources=[
            SourceEvidence(
                id="filmarks_anime",
                title="アニメ『STEINS;GATE』の感想・レビュー",
                url="https://filmarks.com/animes/36/57",
                publisher="Filmarks",
                role="editorial",
            )
        ],
        images=[
            ImageAsset(
                id=f"article-image-{index}",
                url=f"https://filmarks.com/images/{index}.jpg",
                source_url="https://filmarks.com/animes/36/57",
            )
            for index in range(5)
        ],
    )
    kitsu = Source(
        id="kitsu_anime_catalog",
        name="Kitsu Anime Catalog API",
        url="https://kitsu.io/api/edge/anime",
        category="kitsu_anime_catalog",
        role="reference",
        collection="api",
        status="active",
        region="global",
        allowed_asset_hosts=["media.kitsu.app"],
    )
    requested: list[list[int]] = []

    def collect(work_ids, _source, *, max_images):
        requested.append(work_ids)
        return []

    monkeypatch.setattr(cli, "collect_kitsu_work_images", collect)

    cli._add_same_work_catalog_images(package, [kitsu], max_images=20)

    assert package.menu_work_ids == [22]
    assert requested == [[22]]


def test_event_can_collect_exact_work_reference_art_without_relabeling_it(monkeypatch) -> None:
    package = ContentPackage(
        request_id="event-supporting-work-art",
        topic="Event",
        content_kind=ContentKind.INFORMATION,
        content_type="event",
        sources=[
            SourceEvidence(
                id="my_hero_academia_official",
                title="My Hero Academia event announcement",
                url="https://heroaca.com/news/1",
                publisher="Official anime site",
                is_official=True,
                role="primary",
            )
        ],
    )
    kitsu = Source(
        id="kitsu_anime_catalog", name="Kitsu catalog", category="kitsu_anime_catalog",
        url="https://kitsu.io/api/edge/anime", role="reference", collection="api",
        status="active", region="global",
    )
    reference = ImageAsset(
        id="same-work-reference", url="https://media.kitsu.app/poster.jpg",
        source_url="https://kitsu.io/anime/5", menu_work_ids=[5],
        asset_kind="catalog_poster", official=False,
        attribution_text="Kitsu catalog reference; not event photography",
    )
    requests = []

    def collect(work_ids, source, *, max_images):
        requests.append((work_ids, source.id, max_images))
        return [reference]

    monkeypatch.setattr(cli, "collect_kitsu_work_images", collect)
    added = cli._add_same_work_catalog_images(package, [kitsu], max_images=20)

    assert added == 1
    assert package.content_type == "event"
    assert package.menu_work_ids == [5]
    assert requests == [([5], "kitsu_anime_catalog", 8)]
    assert package.images == [reference]
    assert package.images[0].official is False
    assert package.images[0].asset_kind == "catalog_poster"
    assert package.images[0].attribution_text.endswith("not event photography")
