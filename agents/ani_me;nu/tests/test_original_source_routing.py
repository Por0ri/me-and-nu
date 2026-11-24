import json

import pytest

from ani_menu_agent import cli
from ani_menu_agent.cli import _registered_original_source_link
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentType
from ani_menu_agent.sources import page_collector
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import PageOutboundLink


def _source(source_id: str, host: str) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url=f"https://{host}/news/",
        category="official_news",
        content_types=[ContentType.NEWS],
        role="primary",
        collection="html",
        status="active",
        region="JP",
        discovery_enabled=False,
    )


def test_original_link_resolves_only_to_registered_active_type_compatible_primary_source():
    source = _source("work_official", "official.example")
    link = PageOutboundLink(url="https://official.example/news/123", text="公式発表")

    resolved = _registered_original_source_link([link], [source], "news")

    assert resolved == (source, "https://official.example/news/123", "公式発表")
    assert _registered_original_source_link([link], [source], "goods") is None


def test_original_link_resolution_fails_closed_when_registered_targets_are_ambiguous():
    links = [
        PageOutboundLink(url="https://official-one.example/news/1", text="公式発表"),
        PageOutboundLink(url="https://official-two.example/news/2", text="公式発表"),
    ]

    assert _registered_original_source_link(
        links,
        [
            _source("official_one", "official-one.example"),
            _source("official_two", "official-two.example"),
        ],
        "news",
    ) is None


@pytest.mark.parametrize(
    "targets, resolved",
    [
        (["https://unregistered.example/items/1"], False),
        (["https://official.example/items/1", "https://official.example/items/2"], False),
        (["https://official.example/items/1"], True),
    ],
)
def test_dfx_article_needs_one_approved_original_before_handoff(
    monkeypatch, tmp_path, targets, resolved
):
    discovery = next(
        source for source in cli.load_source_registry().sources if source.id == "src_dfx_theshop_jp"
    )
    primary = _source("official_goods", "official.example").model_copy(
        update={"content_types": [ContentType.GOODS]}
    )
    registry = SourceRegistry(version=1, sources=[discovery, primary])
    url = "https://dfx.theshop.jp/items/123"
    item = DiscoveryItem(
        id="dfx-item", source_id=discovery.id, url=url,
        title="진격의 거인 공식 굿즈 안내", content_types=["goods"],
    )
    repo = cli.AgentRepository(path=tmp_path / "routing.sqlite3")
    repo.persist_discovery([item.model_dump(mode="json")], "all")
    output = tmp_path / "result.json"
    links = "".join(f'<a href="{target}">公式発表</a>' for target in targets)
    pages = {
        url: f"<article><h1>진격의 거인 판매 소식</h1><p>발견 기사 내용.</p>{links}</article>",
        "https://official.example/items/1": (
            "<article><h1>진격의 거인 공식 굿즈</h1>"
            "<p>판매 기간: 2090년 3월 7일부터 2090년 3월 21일까지.</p></article>"
        ),
    }
    fetched = []

    def get_html(url, source, timeout):
        fetched.append(url)
        return pages[url], url

    monkeypatch.setattr(page_collector, "_get_html", get_html)
    monkeypatch.setattr(cli, "load_source_registry", lambda: registry)
    monkeypatch.setattr(cli, "AgentRepository", lambda: repo)
    monkeypatch.setattr(cli.get_settings(), "duplicate_baseline_path", None)
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_args: 0)
    monkeypatch.setattr(cli, "image_curator", lambda state: state)
    monkeypatch.setattr(
        cli, "resolve_unmatched_work_with_llm", lambda **kwargs: pytest.fail("Unexpected model call")
    )
    cli.crawl_generate(candidate_id=item.id, content_type="goods", output=output, prepare_only=True)

    result = json.loads(output.read_text(encoding="utf-8"))
    assert repo.load_discovery_candidate(item.id)["url"] == url
    with repo.connect() as db:
        rows = db.execute("SELECT url, raw_payload FROM raw_document").fetchall()
        jobs = db.execute("SELECT request_id FROM pipeline_jobs WHERE stage='generate'").fetchall()
    if resolved:
        assert result["status"] == "generation_pending"
        assert result["source_urls"] == targets
        assert fetched == [url, *targets]
        assert [row[0] for row in jobs] == [result["request_id"]]
        documents = {row[0]: json.loads(row[1]) for row in rows}
        assert documents[url]["capture_role"] == "discovery_context_only"
        assert documents[targets[0]]["discovered_via_source_id"] == discovery.id
        assert documents[targets[0]]["discovery_url"] == url
        assert "발견 기사 내용" not in documents[targets[0]]["text"]
    else:
        assert result["status"] == "original_source_unresolved"
        assert result["capture_role"] == "discovery_context_only"
        assert result["candidate_id"] == item.id
        assert result["discovered_via_source_id"] == discovery.id
        assert result["discovery_url"] == url
        assert result["original_source_url"] is None
        assert [link["url"] for link in result["outbound_links"]] == targets
        assert fetched == [url]
        assert not rows and not jobs
