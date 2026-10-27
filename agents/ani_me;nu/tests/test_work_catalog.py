import json

from ani_menu_agent.models import ContentPackage, SourceEvidence
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog import attach_menu_work_ids


def test_work_ids_resolve_in_representative_first_mention_order() -> None:
    package = ContentPackage(
        request_id="work-links-order",
        topic="나의 히어로 아카데미아와 진격의 거인",
        content_kind="information",
    )

    attach_menu_work_ids(package)

    assert package.menu_work_ids[:2] == [5, 1]


def test_work_ids_are_persisted_as_ordered_primary_links(tmp_path) -> None:
    package = ContentPackage(
        request_id="work-links-storage",
        topic="나의 히어로 아카데미아와 진격의 거인",
        content_kind="information",
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
