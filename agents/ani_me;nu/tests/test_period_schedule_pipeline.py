import json
from datetime import UTC, datetime

import httpx
import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli, storage
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentType, StructuredWorkReference
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import SourcePageCapture


@pytest.mark.parametrize(
    "kind,title,announcement,field,value",
    [
        (ContentType.GOODS, "Naruto 생일 굿즈", "販売期間：2020年11月12日から。終了日は未定です。",
         "sale_start", "2020-11-12"),
        (ContentType.EVENT, "Naruto 팝업스토어", "행사 기간: 2090년 3월 7일부터. 종료일 미정.",
         "event_start", "2090-03-07"),
        (ContentType.NEWS, "Naruto 신작 애니", "2090년 10월 첫 방송.", "premiere_date", "2090-10"),
    ],
)
def test_crawl_generate_queues_partial_source_dates(
    monkeypatch, tmp_path, kind, title, announcement, field, value
):
    repo = cli.AgentRepository(path=tmp_path / "period.sqlite3")
    url = "https://news.example/naruto/announcement"
    source = Source(id="official_news", name="Official news", url="https://news.example/",
                    category="official_news", content_types=[kind], role="primary", collection="html",
                    status="active", region="JP")
    stamp = datetime.now(UTC).isoformat()
    item = DiscoveryItem(id="dated-item", source_id=source.id, content_types=[kind.value], title=title,
                         url=url, published_at=stamp)
    capture = SourcePageCapture(
        source_id=source.id, title=title, url=url, text=announcement, image_urls=[],
        fetched_at=stamp, published_at=stamp,
        structured_work_references=[StructuredWorkReference(relation="about", name="Naruto")],
    )
    repo.persist_discovery([item.model_dump(mode="json")], "mixed")
    monkeypatch.setattr(cli, "AgentRepository", lambda: repo)
    monkeypatch.setattr(storage.get_settings(), "duplicate_baseline_path", None)
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *args, **kwargs: capture)
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "image_curator", lambda state: state)

    def no_external_io(*args, **kwargs):
        pytest.fail("The crawler test must use the fake page and local catalog")

    monkeypatch.setattr(httpx.Client, "send", no_external_io)
    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", no_external_io)

    result_path = tmp_path / "result.json"
    run = CliRunner().invoke(cli.app, ["crawl-generate", "--candidate-id", item.id,
                                      "--content-type", kind.value, "--prepare-only",
                                      "--output", str(result_path)])
    assert run.exit_code == 0, (run.output, run.exception)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "generation_pending", result
    assert result["menu_work_ids"] == [11]
    with repo.connect() as conn:
        raw, published, start, end = conn.execute(
            "SELECT raw_text, published_at, starts_at, ends_at FROM raw_document "
            "JOIN raw_document_schedule USING (raw_document_id) WHERE url=?", (url,),
        ).fetchone()
        assert announcement in raw
        assert published == stamp
        assert (start, end) == (value, None)
        request_id = result["request_id"]
        fact = conn.execute(
            "SELECT payload_json FROM agent_records WHERE request_id=? AND record_kind='fact' "
            "AND json_extract(payload_json, '$.fact_type')='source_schedule'", (request_id,),
        ).fetchone()
        assert json.loads(fact[0])["details"] == {field: value}
        job = conn.execute(
            "SELECT stage,status FROM pipeline_jobs WHERE request_id=?", (request_id,),
        ).fetchone()
        assert tuple(job) == ("generate", "pending")
