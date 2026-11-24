import pytest

from ani_menu_agent.models import ContentPackage
from ani_menu_agent.work_catalog import (
    UnconfirmedPeriodWorkError,
    attach_menu_work_ids,
    rematch_returned_goods,
    resolve_work_matches,
)


ARTICLE = "https://animeanime.jp/article/2026/11/23/105037.html"
TITLE = "「無職転生」ロキシー師匠がグレイラット家での幸せなひと時をイメージした姿で立体化！ シルフィ＆ルーシーをあわせて飾っても◎"


def returned_goods():
    return ContentPackage.model_validate({
        "request_id": "returned-roxy", "topic": TITLE, "content_type": "goods", "content_kind": "information",
        "sources": [{"id": "anime_anime_goods", "url": ARTICLE, "publisher": "アニメ！アニメ！", "title": TITLE}],
        "facts": [
            {"id": "raw", "source_ids": ["anime_anime_goods"], "statement": TITLE,
             "fact_type": "source_page_capture", "details": {"captured_url": ARTICLE}},
            {"id": "old", "source_ids": ["anime_anime_goods"], "statement": "예전 작품에서 추론한 설명"},
        ],
        "images": [{"id": "old-art", "menu_work_ids": [19], "source_url": ARTICLE, "url": ARTICLE + "/art.jpg"}],
        "menu_work_ids": [19],
        "work_match_evidence": [{"menu_work_id": 19, "basis": "headline_character",
                                 "matched_value": "シルフィ", "confidence": 0.92}],
    })


def test_roxy_article_title_beats_other_character_and_recollection_discards_old_inputs():
    matches = resolve_work_matches(titles=[TITLE], source_url=ARTICLE, content_type="goods")
    assert [(item.menu_work_id, item.basis) for item in matches] == [(50, "article_title")]
    package = returned_goods()
    with pytest.raises(UnconfirmedPeriodWorkError):
        attach_menu_work_ids(package, check_period_artwork=True)
    assert rematch_returned_goods(package)
    assert package.menu_work_ids == [50]
    assert package.work_match_evidence[0].basis == "article_title"
    assert str(package.work_match_evidence[0].evidence_url) == ARTICLE
    assert package.images == []
    assert [fact.id for fact in package.facts] == ["raw"]


def test_goods_identity_repair_requires_original_capture_and_weak_old_match():
    package = returned_goods()
    package.facts[0].details["captured_url"] = "https://example.org/other"
    assert not rematch_returned_goods(package)
    package = returned_goods()
    package.work_match_evidence[0].basis = "article_title"
    assert not rematch_returned_goods(package)
    assert package.menu_work_ids == [19]
