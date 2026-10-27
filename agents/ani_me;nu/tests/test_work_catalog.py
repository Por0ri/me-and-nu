import json

from ani_menu_agent.models import ContentPackage, SourceEvidence, StructuredWorkReference
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import PageOutboundLink
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog import (
    _registered_work_titles,
    attach_menu_work_ids,
    resolve_work_matches,
)


def test_work_ids_resolve_in_representative_first_mention_order() -> None:
    package = ContentPackage(
        request_id="work-links-order",
        topic="공식 콜라보 소식",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="publisher_release",
                title="나의 히어로 아카데미아 × 진격의 거인",
                url="https://publisher.example/news/1",
                publisher="Publisher",
                is_official=True,
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids[:2] == [5, 1]


def test_work_ids_are_persisted_as_ordered_primary_links(tmp_path) -> None:
    package = ContentPackage(
        request_id="work-links-storage",
        topic="공식 콜라보 소식",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="publisher_release",
                title="나의 히어로 아카데미아 × 진격의 거인",
                url="https://publisher.example/news/1",
                publisher="Publisher",
                is_official=True,
                role="primary",
            )
        ],
    )
    repository = AgentRepository(path=tmp_path / "agent.sqlite3")

    repository.persist_generated_content(package, "generated_pending_review")

    with repository.connect() as connection:
        links = connection.execute(
            "SELECT menu_work_id, sort_order, is_primary FROM content_work_links "
            "WHERE request_id=? ORDER BY sort_order",
            (package.request_id,),
        ).fetchall()
        payload = connection.execute(
            "SELECT package_json FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()

    assert [(row["menu_work_id"], row["sort_order"], row["is_primary"]) for row in links] == [
        (5, 1, 1),
        (1, 2, 0),
    ]
    assert json.loads(payload["package_json"])["menu_work_ids"] == [5, 1]


def test_unknown_work_title_is_not_guessed() -> None:
    package = ContentPackage(
        request_id="work-links-unknown",
        topic="완전히 새로운 가상 작품",
        content_kind="information",
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == []


def test_registered_original_work_title_is_tried_after_unmatched_korean_title(
    monkeypatch, request,
) -> None:
    source = Source(
        id="original_title_only",
        name="Official source",
        url="https://official.example/news",
        category="official_news",
        content_types=[],
        role="primary",
        collection="html",
        status="active",
        region="JP",
        work_title_ko="Unmapped Korean alias",
        work_title_original="Naruto",
    )
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )
    _registered_work_titles.cache_clear()
    request.addfinalizer(_registered_work_titles.cache_clear)
    package = ContentPackage(
        request_id="work-links-original-title-fallback",
        topic="Official event announcement",
        content_kind="information",
        sources=[
            SourceEvidence(
                id=source.id,
                title="Official event announcement",
                url="https://official.example/news/1",
                publisher="Official source",
                is_official=True,
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [11]


def test_structured_about_reference_resolves_and_records_auditable_basis(monkeypatch) -> None:
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[]),
    )

    matches = resolve_work_matches(
        titles=["Limited cafe goods announcement"],
        structured_references=[
            StructuredWorkReference(relation="about", name="Naruto")
        ],
    )

    assert len(matches) == 1
    assert matches[0].menu_work_id == 11
    assert matches[0].basis == "structured_about"
    assert matches[0].confidence == 0.94
    assert matches[0].matched_value == "Naruto"


def test_registered_official_link_can_resolve_work_when_article_title_omits_it(
    monkeypatch,
) -> None:
    source = Source(
        id="naruto_official",
        name="Naruto Official",
        url="https://naruto-official.example/news/",
        category="official_news",
        content_types=["event"],
        role="primary",
        collection="html",
        status="active",
        region="JP",
        work_title_ko="나루토",
        work_title_original="Naruto",
    )
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )

    matches = resolve_work_matches(
        titles=["Collaboration cafe announced"],
        outbound_links=[
            PageOutboundLink(
                url="https://naruto-official.example/news/123",
                text="Official site",
            )
        ],
        content_type="event",
    )

    assert len(matches) == 1
    assert matches[0].menu_work_id == 11
    assert matches[0].basis == "registered_official_link"
    assert matches[0].confidence == 0.97
    assert str(matches[0].evidence_url) == "https://naruto-official.example/news/123"


def test_article_title_match_takes_precedence_over_metadata_matches(monkeypatch) -> None:
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[]),
    )

    matches = resolve_work_matches(
        titles=["Naruto event announcement"],
        structured_references=[
            StructuredWorkReference(relation="about", name="Frieren")
        ],
    )

    assert [item.menu_work_id for item in matches] == [11]
    assert all(item.basis == "article_title" for item in matches)


def test_one_article_resolves_to_one_work_without_explicit_collaboration() -> None:
    package = ContentPackage(
        request_id="work-links-single-default",
        topic="Discussion",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="publisher_release",
                title="Naruto and Bleach discussion",
                url="https://publisher.example/news/2",
                publisher="Publisher",
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [11]


def test_explicit_work_collaboration_can_resolve_multiple_work_ids() -> None:
    package = ContentPackage(
        request_id="work-links-explicit-collaboration",
        topic="Collaboration announcement",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="publisher_release",
                title="Naruto × Bleach collaboration",
                url="https://publisher.example/news/3",
                publisher="Publisher",
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [11, 37]


def test_multiple_structured_work_references_do_not_override_single_work_default(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[]),
    )

    matches = resolve_work_matches(
        titles=["Discussion about Naruto and Bleach"],
        structured_references=[
            StructuredWorkReference(relation="about", name="Naruto"),
            StructuredWorkReference(relation="about", name="Bleach"),
        ],
    )

    assert [item.menu_work_id for item in matches] == [11]


def test_explicit_collaboration_title_allows_multiple_structured_work_references(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[]),
    )

    matches = resolve_work_matches(
        titles=["Naruto × Bleach collaboration"],
        structured_references=[
            StructuredWorkReference(relation="about", name="Naruto"),
            StructuredWorkReference(relation="about", name="Bleach"),
        ],
    )

    assert [item.menu_work_id for item in matches] == [11, 37]


def test_article_title_with_roman_numeral_sequel_resolves_canonical_work_id() -> None:
    package = ContentPackage(
        request_id="work-links-sequel-suffix",
        topic="굿즈 발매 소식",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="prtimes_release_pages",
                title="アニメ『終末のワルキューレIII』オリジナルグッズ発売！",
                url="https://prtimes.jp/main/html/p/1.html",
                publisher="PR TIMES",
                is_official=True,
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [358]


def test_colon_punctuation_variant_with_sequel_resolves_canonical_work_id() -> None:
    package = ContentPackage(
        request_id="work-links-punctuation-sequel",
        topic="review",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="filmarks_anime",
                title="Cyberpunk: Edgerunners 2",
                url="https://filmarks.com/animes/3610/6583",
                publisher="Filmarks",
                role="editorial",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [70]


def test_longest_work_title_match_does_not_link_nested_another_title() -> None:
    package = ContentPackage(
        request_id="work-links-longest-overlap",
        topic="Review",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="anime_feminist_reviews",
                title="Full Clearing Another World under a Goddess with Zero Believers – Episode 1",
                url="https://reviews.example/full-clearing-another-world-episode-1",
                publisher="Anime Feminist",
                role="editorial",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [2161]


def test_semicolon_compound_title_does_not_match_unrelated_suffix_title() -> None:
    package = ContentPackage(
        request_id="work-links-semicolon-compound",
        topic="Review",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="filmarks_anime",
                title="STEINS;GATE Review",
                url="https://filmarks.com/animes/36/57",
                publisher="Filmarks",
                role="editorial",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [22]


def test_topic_or_body_text_is_not_used_as_a_work_title_fallback() -> None:
    package = ContentPackage(
        request_id="work-links-topic-not-title",
        topic="나의 히어로 아카데미아 특집",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="publisher_release",
                title="새 시즌 제작진 인터뷰",
                url="https://publisher.example/news/2",
                publisher="Publisher",
                is_official=True,
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == []


def test_work_links_ignore_incidental_titles_in_body_and_use_article_title_first() -> None:
    package = ContentPackage(
        request_id="work-links-title-only",
        topic="굿즈 발매 정보",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="one_piece_official",
                title="ONE PIECE 가을·겨울 의류 발매",
                url="https://one-piece.com/news/82812/index.html",
                publisher="ONE PIECE.com",
                is_official=True,
                published_at="2026-10-19",
                role="primary",
            )
        ],
        facts=[],
    )
    package.facts = []
    package.cards = []

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [8]


def test_body_only_work_mention_does_not_create_entity_link() -> None:
    package = ContentPackage(
        request_id="work-links-no-body-match",
        topic="굿즈 발매 정보",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="orb_cafe_official",
                title="가을 행사 안내",
                url="https://orb-cafe.com/news/",
                publisher="ORBcafe",
                is_official=True,
                published_at="2026-10-19",
                role="primary",
            )
        ],
        facts=[
            {
                "id": "f1",
                "statement": "나루토라는 단어가 우연히 본문에 등장한다",
                "source_ids": ["orb_cafe_official"],
            }
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == []


def test_japanese_character_name_substring_does_not_match_another_work() -> None:
    package = ContentPackage(
        request_id="work-links-japanese-name-boundary",
        topic="나루토 생일 축제",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="naruto_official",
                title=(
                    "忍里うずまきナルト誕生祭2026 | NARUTO OFFICIAL SITE、"
                    "NARUTO -ナルト-オフィシャルサイト、BORUTO -ボルト-"
                ),
                url="https://naruto-official.com/news/01_2705",
                publisher="NARUTO OFFICIAL SITE",
                is_official=True,
                published_at="2026-10-15",
                role="primary",
            )
        ],
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids == [11]
