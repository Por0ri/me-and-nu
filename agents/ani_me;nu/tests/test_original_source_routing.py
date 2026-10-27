from ani_menu_agent.cli import _registered_original_source_link
from ani_menu_agent.models import ContentType
from ani_menu_agent.sources.models import Source
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
