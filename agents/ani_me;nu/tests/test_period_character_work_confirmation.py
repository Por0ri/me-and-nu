import json
from datetime import UTC, datetime

import httpx
import pytest

from ani_menu_agent import cli, work_aliases
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentKind, ContentPackage, ContentType, SourceEvidence, WorkMatchEvidence
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import PageLinkedEvidence, SourcePageCapture


@pytest.fixture(autouse=True)
def character_dictionary(tmp_path, monkeypatch):
    (tmp_path / "work_characters.csv").write_text(
        "menu_work_id,name\n11,ロック・リー\n", encoding="utf-8"
    )
    monkeypatch.setattr(work_aliases, "_REFERENCE", tmp_path)
    work_aliases._character_index.cache_clear()
    work_aliases._song_indexes.cache_clear()
    yield
    work_aliases._character_index.cache_clear()
    work_aliases._song_indexes.cache_clear()


@pytest.mark.parametrize("entry,model_id", [("candidate", None), ("url", 11), ("preflight", 50)])
def test_character_only_work_requires_original_article_confirmation(monkeypatch, tmp_path, entry, model_id):
    source = Source(id="product_news", name="Product news", url="https://goods.example/",
                    category="official_news", content_types=[ContentType.GOODS], role="editorial",
                    collection="html", status="active", region="JP")
    url = "https://goods.example/news/123"
    title = "「ロック・リー」のグッズ"
    main_text = "販売開始：2090年3月7日。商品案内では原作とキャラクターを紹介しています。"
    capture = SourcePageCapture(
        source_id=source.id, url=url, title=title, fetched_at=datetime.now(UTC).isoformat(),
        text=main_text + "\n\n[연결된 상세 원문: BLEACH | https://shop.example/items/2]\nBLEACH",
        main_text=main_text, image_urls=[], linked_page_evidence=[PageLinkedEvidence(
            title="BLEACH", url="https://shop.example/items/2", linked_from_url=url,
            link_label="おすすめ商品", text="BLEACH",
        )],
    )
    candidate = DiscoveryItem(
        id="product-123", source_id=source.id, content_types=["goods"], url=url,
        title="新商品" if entry == "preflight" else "BLEACH 공식 굿즈",
        published_at=capture.fetched_at,
    )
    repo = cli.AgentRepository(path=tmp_path / "period.sqlite3")
    repo.persist_discovery([candidate.model_dump(mode="json")], "goods")
    monkeypatch.setattr(cli, "AgentRepository", lambda: repo)
    monkeypatch.setattr(cli.get_settings(), "duplicate_baseline_path", None)
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *args, **kwargs: capture)
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "image_curator", lambda state: state)
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Unexpected network request"))
    calls = []

    def resolve(**kwargs):
        calls.append(kwargs)
        assert kwargs["titles"] == [title]
        assert kwargs["body_excerpt"] == main_text
        assert kwargs["source_url"] == url
        assert [h.menu_work_id for h in kwargs["character_hints"]] == [11]
        return [WorkMatchEvidence(menu_work_id=model_id, basis="llm_body_context",
                                  matched_value="supported title", evidence_url=url,
                                  confidence=0.95)] if model_id else []

    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", resolve)
    output = tmp_path / "capture.json"
    args = {"source_id": source.id, "url": url} if entry == "url" else {"candidate_id": candidate.id}
    cli.crawl_generate(**args, content_type="goods", prepare_only=True, output=output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 1, result
    with repo.connect() as db:
        raw = db.execute("SELECT raw_payload FROM raw_document WHERE url=?", (url,)).fetchone()
        jobs = db.execute("SELECT count(*) FROM pipeline_jobs WHERE stage='generate'").fetchone()[0]
    if model_id:
        assert result["status"] == "generation_pending"
        assert jobs == 1
        stored = json.loads(raw[0])
        assert stored["menu_work_ids"] == [model_id]
        assert stored["work_match_evidence"][0]["basis"] == "llm_body_context"
    else:
        assert result["status"] == "work_id_unresolved_skipped"
        assert raw is None and jobs == 0


def test_changed_article_cannot_restore_held_work_by_character_name_alone(monkeypatch):
    source = Source(id="product_news", name="Product news", url="https://goods.example/",
                    category="official_news", content_types=[ContentType.GOODS], role="editorial",
                    collection="html", status="active", region="JP")
    url = "https://goods.example/news/123"
    package = ContentPackage(request_id="held", topic="Naruto", content_type="goods",
                             content_kind=ContentKind.INFORMATION, menu_work_ids=[11],
                             sources=[SourceEvidence(id=source.id, title="Naruto", url=url,
                                                     publisher=source.name)])
    capture = SourcePageCapture(source_id=source.id, title="「ロック・リー」のグッズ", url=url,
                                text="販売開始：2090年3月7日。", fetched_at=datetime.now(UTC).isoformat(),
                                image_urls=[])
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *a, **kw: capture)
    with pytest.raises(cli.UnconfirmedPeriodWorkError):
        cli.recollect_period_evidence(None, package)
