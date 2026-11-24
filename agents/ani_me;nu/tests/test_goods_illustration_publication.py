import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import HttpUrl

from ani_menu_agent import goods_publication
from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.markdown_export import AI_DISCLOSURE_TEXT, render_detail_markdown
from ani_menu_agent.models import ContentPackage, Fact, SourceEvidence
from ani_menu_agent.period_copy import authoring_policy
from ani_menu_agent.storage import AgentRepository

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
PRODUCT = "https://www.kotobukiya.co.jp/product/detail/p4934054079544/"


@pytest.fixture
def goods(monkeypatch):
    package = ContentPackage.model_validate_json(SAMPLE.read_bytes())
    package.content_type = "goods"
    package.cards = package.cards[:2]
    package.cards[-1].role = "ending"
    package.images = package.images[:2]
    package.images[0].asset_kind = "character_art"
    package.images[1].asset_kind = "goods_product"
    package.detail_body = "록시 피규어 예약이 열렸어."
    # These tests exercise the publication boundary with known upstream decisions.
    monkeypatch.setattr(
        goods_publication,
        "goods_reference_image_label",
        lambda img, pkg: {"character_art": "작품 일러스트", "catalog_poster": "작품 포스터"}.get(img.asset_kind, ""),
    )
    monkeypatch.setattr(
        goods_publication.period_images, "verified_goods_product_url", lambda pkg: PRODUCT
    )
    return package


def test_labels_and_verified_link_reach_sqlite_with_credits(goods, tmp_path):
    repo = AgentRepository(path=tmp_path / "goods.sqlite3")
    repo.persist_generated_content(goods, "generated_pending_review")
    with repo.connect() as db:
        saved = json.loads(db.execute("SELECT package_json FROM content_drafts").fetchone()[0])
        overlays = [
            json.loads(r[0])
            for r in db.execute("SELECT overlay_json FROM content_cards ORDER BY page")
        ]
        cards = db.execute(
            "SELECT image_credit,source_page_url FROM content_card ORDER BY page_no"
        ).fetchall()
        body = db.execute("SELECT body FROM content").fetchone()[0]
    assert saved["cards"][0]["image_label"] == overlays[0]["image_label"] == "작품 일러스트"
    assert not overlays[1].get("image_label")
    assert cards[0][0] == "작품 일러스트 · " + goods.images[0].attribution_text
    assert cards[1][0] == goods.images[1].attribution_text
    assert cards[0][1] == str(goods.images[0].source_url)
    assert body.endswith(PRODUCT) and "](" not in body
    detail = render_detail_markdown(goods)
    assert detail.index(PRODUCT) < detail.index("## 출처") < detail.index("이미지\n")
    assert str(goods.sources[0].url) in detail
    assert detail.rstrip().endswith(AI_DISCLOSURE_TEXT)
    repo.enqueue_for_feed(goods)
    published = repo.active_feed_items()[0]["package"]
    assert published["cards"][0]["image_label"] == "작품 일러스트"
    assert published["detail_article"]["product_url"] == PRODUCT
    assert published["detail_markdown"] == detail

    # Layout repair changes which image a particular card actually uses.
    goods.images[0].asset_kind = "goods_product"
    repo.persist_generated_content(goods, "generated_pending_review")
    assert goods.cards[0].image_label == ""
    assert PRODUCT not in render_detail_markdown(goods)
    with repo.connect() as db:
        assert db.execute("SELECT body FROM content").fetchone()[0] == goods.detail_body
        assert (
            db.execute("SELECT image_credit FROM content_card WHERE page_no=1").fetchone()[0]
            == goods.images[0].attribution_text
        )


def test_missing_product_url_keeps_original_and_image_credits(goods, monkeypatch):
    monkeypatch.setattr(
        goods_publication.period_images, "verified_goods_product_url", lambda pkg: None
    )
    detail = render_detail_markdown(goods)
    assert "제조사 상품 페이지" not in detail
    assert str(goods.sources[0].url) in detail and goods.images[0].attribution_text in detail
    assert detail.rstrip().endswith(AI_DISCLOSURE_TEXT)


def test_poster_label_and_independent_credit_survive_publication(goods, tmp_path):
    goods.images[0].asset_kind = "catalog_poster"
    goods.images[0].source_url = HttpUrl("https://kitsu.io/api/edge/anime/75")
    goods.cards[0].body_sentences = ["이 포스터는 제품 실물이야!"]
    assert [issue.page for issue in goods_publication.goods_illustration_copy_issues(goods)] == [1]
    goods.cards[0].body_sentences = ["볼트 건담 피규어가 6월에 나올 예정이야."]
    repo = AgentRepository(path=tmp_path / "goods.sqlite3")
    repo.persist_generated_content(goods, "generated_pending_review")
    with repo.connect() as db:
        overlay = json.loads(db.execute("SELECT overlay_json FROM content_cards WHERE page=1").fetchone()[0])
        credit, page_url = db.execute("SELECT image_credit,source_page_url FROM content_card WHERE page_no=1").fetchone()
    assert goods.cards[0].image_label == overlay["image_label"] == "작품 포스터"
    assert credit == "작품 포스터 · " + goods.images[0].attribution_text
    assert page_url == str(goods.images[0].source_url)


def test_misdescribed_illustration_returns_only_its_copy_before_paid_review(goods, monkeypatch):
    for card in goods.cards:
        card.body_sentences = ["이 사진은 제품 실물이야!"]
    monkeypatch.setattr(reviewer, "get_settings", lambda: SimpleNamespace(openai_api_key=""))
    monkeypatch.setattr(
        reviewer,
        "AgentRepository",
        lambda: SimpleNamespace(persist_generated_content=lambda *args, **kw: None),
    )
    result = reviewer._review_minimal_editorial({}, goods)
    assert result["editorial_decision"] == "revise"
    assert [(issue["page"], issue["owner"]) for issue in result["qa_report"]] == [
        (1, "copy_writer")
    ]

    goods.cards[0].body_sentences = ["지팡이를 든 록시 피규어래. 예약은 24일부터야!"]
    assert not goods_publication.goods_illustration_copy_issues(goods)
    goods.cards[0].body_sentences = ["이 포즈 그대로 피규어로 나왔어!"]
    assert [issue.page for issue in goods_publication.goods_illustration_copy_issues(goods)] == [1]


def test_verified_source_and_matched_character_drive_the_reader_payload_without_model():
    package = ContentPackage.model_validate_json(SAMPLE.read_bytes())
    article = SourceEvidence(
        id="anime_anime_goods",
        title="『無職転生』ロキシーのフィギュア",
        publisher="Anime! Anime!",
        url="https://animeanime.jp/article/2026/11/23/105038.html",
        role="editorial",
    )
    character = SourceEvidence(
        id="official-character",
        publisher="공식 애니메이션 사이트",
        title="ロキシー",
        url="https://work.example/characters/roxy",
        is_official=True,
        role="primary",
        link_relationship_type="character",
        target_work_match=True,
    )
    package.content_type = "goods"
    package.menu_work_ids = [50]
    package.sources = [article]
    package.facts = [
        Fact(
            id="source",
            statement="록시 피규어의 상품 공지",
            source_ids=[article.id],
            fact_type="source_page_capture",
            details={"goods_subject": "ロキシー", "verified_product_url": PRODUCT},
        )
    ]
    package.cards = package.cards[:1]
    package.images = [
        package.images[0].model_copy(
            update={
                "url": "https://work.example/assets/roxy.jpg",
                "source_url": character.url,
                "asset_kind": "character_art",
                "menu_work_ids": [50],
                "depicted_entities": ["ロキシー"],
                "provenance_source": character,
            }
        )
    ]
    package.detail_body = "록시 피규어 예약이 열렸어."
    goods_publication.set_goods_image_labels(package)
    assert package.cards[0].image_label == "작품 일러스트"
    assert "Product features" in authoring_policy(package, [])
    detail = render_detail_markdown(package)
    assert detail.count(PRODUCT) == 1
    assert str(article.url) in detail and str(character.url) in detail
    assert detail.rstrip().endswith(AI_DISCLOSURE_TEXT)

    package.images[0].depicted_entities = ["エリス"]
    goods_publication.set_goods_image_labels(package)
    assert package.cards[0].image_label == ""
    assert PRODUCT not in render_detail_markdown(package)
    assert "Product features" not in authoring_policy(package, [])
