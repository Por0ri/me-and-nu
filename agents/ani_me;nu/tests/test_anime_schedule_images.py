from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.image_selection import image_belongs_to_package_sources
from ani_menu_agent.models import ContentPackage, ImageAsset, SourceEvidence
from ani_menu_agent.sources import load_source_registry, page_collector


SLUG = "tensei-kizoku-kantei-skill-de-nariagaru-3rd-season"
PAGE = "https://animeschedule.net/anime/" + SLUG
CDN = "https://img.animeschedule.net/production/assets/public/img"
POSTER = f"{CDN}/anime/jpg/default/{SLUG}-7689b9229a0d8f6d9e85b5ce56bf6e84.jpg"
PREQUEL = f"{CDN}/anime/jpg/default/tensei-kizoku-kantei-skill-de-nariagaru-2nd-season-b789a94bcb.jpg"
AD = f"{CDN}/media/q3v8nz6h/wide-default-global-65130de576.jpg"


@pytest.mark.parametrize("poster_present", [True, False])
def test_profile_capture_excludes_sponsored_css_and_other_seasons(monkeypatch, poster_present):
    source = next(s for s in load_source_registry().sources if s.id == "anime_schedule_rss")
    source = source.model_copy(update={"collection": "html"})
    hero = f'<img id="anime-poster" src="{POSTER}" alt="Current poster">' if poster_present else ""
    html = f"""<html><head><title>Series</title><meta property="og:image" content="{AD}"></head>
    <body>{hero}<main><h1>Series season 3</h1><a rel="sponsored" href="https://go.nordvpn.net/"
    style="background-image: url('{AD}')">NordVPN advertisement</a>
    <a href="/anime/other-series-2"><img class="related-anime-thumbnail" src="{PREQUEL}">Other series</a>
    </main></body></html>"""
    get = Mock(return_value=(html, PAGE))
    monkeypatch.setattr(page_collector, "_get_html", get)
    capture = page_collector.collect_official_page(source, PAGE, allow_discovery_page=True)
    assert [str(url) for url in capture.image_urls] == ([POSTER] if poster_present else [])
    assert not capture.related_image_pages
    assert get.call_count == 1


def test_profile_uses_own_social_poster_when_the_hero_is_missing():
    soup = BeautifulSoup(f'<meta property="og:image" content="{POSTER}">', "html.parser")
    assert page_collector._image_candidates(soup, PAGE) == ([POSTER], {})
    soup.meta["content"] = PREQUEL
    assert page_collector._image_candidates(soup, PAGE) == ([], {})


def test_legacy_schedule_assets_are_checked_even_with_a_matching_work_id(monkeypatch):
    source = SourceEvidence(id="schedule", title="Season 3", publisher="AnimeSchedule", url=PAGE)
    def asset(identifier, url):
        return ImageAsset(id=identifier, url=url, source_url=PAGE, menu_work_ids=[667])

    poster, prequel, ad = asset("poster", POSTER), asset("prequel", PREQUEL), asset("ad", AD)
    assert image_belongs_to_package_sources(poster, [source])
    assert not image_belongs_to_package_sources(prequel, [source])
    assert not image_belongs_to_package_sources(ad, [source])
    # A regular article elsewhere can legitimately use its own banner-like filename.
    other = SourceEvidence(id="other", title="Other release", publisher="Other", url="https://other.example/news")
    banner = ImageAsset(id="banner", source_url=other.url, url="https://other.example/media/wide-default-global-a.jpg")
    assert image_belongs_to_package_sources(banner, [other])

    example = Path(__file__).resolve().parents[1] / "examples/sample_content.json"
    draft = ContentPackage.model_validate_json(example.read_text(encoding="utf-8"))
    draft.sources, draft.images, draft.cards = [source], [poster, prequel, ad], draft.cards[:2]
    draft.cards[0].image_id, draft.cards[1].image_id = poster.id, ad.id
    repository = Mock()
    monkeypatch.setattr(reviewer, "get_settings", lambda: SimpleNamespace(openai_api_key="fixture"))
    monkeypatch.setattr(reviewer, "AgentRepository", lambda: repository)
    result = reviewer.review_content({"package": draft.model_dump(mode="json"), "minimal_review": True})
    assert result["editorial_decision"] == "revise"
    assert [(issue["code"], issue["page"]) for issue in result["qa_report"]] == [("IMAGE_SOURCE_PAGE_MISMATCH", 2)]
