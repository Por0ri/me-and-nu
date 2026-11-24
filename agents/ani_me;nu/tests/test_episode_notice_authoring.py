import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ani_menu_agent import cli, generation
from ani_menu_agent.agents.content_generation import detail_writer, graph
from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.period_copy import episode_evidence_missing, reported_terms
from ani_menu_agent.quality import check_detail_body
from ani_menu_agent.sources.page_collector import SourcePageCapture

SERIES = "Tensei Kizoku, Kantei Skill de Nariagaru 3rd Season"
TITLE = f"Episode 9 of {SERIES} is out!"
SUMMARY = f"Episode 9 of {SERIES} has been released natively!"
URL = "https://animeschedule.net/anime/tensei-kizoku-kantei-skill-de-nariagaru-3rd-season"
POSTED = "2026-11-22T15:00:00+00:00"


@pytest.fixture
def episode():
    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "sample_content.json").read_text(
            encoding="utf-8"
        )
    )
    raw.update(
        topic=TITLE,
        content_type="news",
        content_kind="information",
        menu_work_ids=[667],
        detail_body="",
        sources=[
            dict(
                id="anime_schedule_rss",
                title=TITLE,
                publisher="AnimeSchedule RAW RSS",
                url=URL,
                is_official=False,
                role="reference",
                discovered_via_source_id="anime_schedule_rss",
                published_at=POSTED,
                discovery_title=TITLE,
                feed_entry_guid=f"{SERIES} Episode 9",
            )
        ],
        facts=[
            dict(
                id="episode-item",
                fact_type="source_page_capture",
                statement=TITLE + "\n" + SUMMARY,
                source_ids=["anime_schedule_rss"],
                verified=False,
                details={
                    "captured_title": TITLE,
                    "captured_url": URL,
                    "published_at": POSTED,
                    "verification_status": "not_performed",
                },
            )
        ],
    )
    return ContentPackage.model_validate(raw)


@pytest.mark.parametrize("module", [generation, detail_writer])
def test_actual_writer_messages_carry_the_item_quotes_and_feed_post_time(
    episode, monkeypatch, module
):
    # Richer packages still go through both ordinary writers.
    episode.facts.append(
        episode.facts[0].model_copy(
            update={
                "id": "extra",
                "fact_type": "creator_comment",
                "statement": "The director discussed the score.",
                "verified": True,
            }
        )
    )
    messages = []

    def invoke(request):
        messages.extend(request)
        raise RuntimeError("fixture captured")

    monkeypatch.setattr(
        module,
        "ChatOpenAI",
        lambda **_: SimpleNamespace(
            with_structured_output=lambda *a, **kw: SimpleNamespace(invoke=invoke)
        ),
    )
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(openai_api_key="fixture", model_name="fixture"),
    )
    monkeypatch.setattr(module, "load_work_catalog", lambda: [])
    if module is generation:
        monkeypatch.setattr(generation, "copy_image_pool", lambda pkg, **_: pkg.images[:2])
        monkeypatch.setattr(generation, "attach_menu_work_ids", lambda _: None)
        monkeypatch.setattr(generation, "supported_content_types_for_sources", lambda _: {"news"})
        monkeypatch.setattr(
            generation,
            "AgentRepository",
            lambda: SimpleNamespace(
                prior_cover_image_fingerprints=lambda **_: [],
                recent_copy_examples=lambda **_: [],
                active_content_types=lambda: [{"code": "news"}],
            ),
        )
    with pytest.raises(RuntimeError, match="fixture captured"):
        if module is generation:
            generation.generate_copy_deck(episode, requested_content_type="news")
        else:
            detail_writer.write_detail_body(
                {"package": episode.model_dump(mode="json"), "allow_llm": True}
            )
    data = json.loads(messages[-1][1].rsplit("\n", 1)[-1])
    notice, edition = data["reported_terms"]
    assert notice["excerpt"] == TITLE and edition["excerpt"] == SUMMARY
    assert edition["edition"] == "original_language" and notice["fact_id"] == "episode-item"
    assert notice["feed_posted_at"] == POSTED
    assert notice["feed_posted_at_kst"] == "2026-11-23T00:00:00+09:00"
    assert not notice["verified"] and not edition["verified"]
    assert "streaming service" in messages[0][1]
    if module is detail_writer:
        assert data["facts"][0]["source_excerpt"] == TITLE + "\n" + SUMMARY
    assert not episode_evidence_missing(episode)


@pytest.mark.parametrize("revision", [False, True])
def test_title_only_returns_to_crawler_before_either_paid_writer(episode, revision, monkeypatch):
    episode.facts[0].statement = TITLE

    def paid(*args, **kwargs):
        pytest.fail("A title-only item must return to the crawler before the writers")

    monkeypatch.setattr(graph, "copy_writer", paid)
    monkeypatch.setattr(graph, "write_detail_body", paid)
    result = graph.build_content_generation_agent().invoke(
        {
            "package": episode.model_dump(mode="json"),
            "allow_llm": True,
            "needs_revision": revision,
            "revision_owners": ["detail_writer"] if revision else [],
        }
    )
    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "EPISODE_NOTICE_EVIDENCE_MISSING"


def test_profile_date_or_another_item_cannot_supply_episode_evidence(episode):
    episode.facts[0].details["published_at"] = "2026-01-05T12:00:00+09:00"
    assert "feed_posted_at" not in reported_terms(episode)[0]
    episode.sources[0].feed_entry_guid = f"{SERIES} Episode 8"
    assert reported_terms(episode) == []
    assert episode_evidence_missing(episode)
    episode.sources[0].id = "other_editorial_news"
    assert not episode_evidence_missing(episode)


@pytest.mark.parametrize("same_item", [True, False])
def test_recollection_reads_the_exact_episode_from_a_refreshed_listing(
    episode, monkeypatch, same_item
):
    source = episode.sources[0]
    episode.facts[0].statement = TITLE
    profile = SourcePageCapture(
        source_id=source.id,
        url=URL,
        title=SERIES,
        text="Series premiere 2025-01-03.",
        published_at="2025-01-03",
        fetched_at="2026-11-23T17:00:00Z",
        image_urls=[],
    )
    item = dict(
        source_id=source.id,
        url=URL,
        title=TITLE,
        summary=SUMMARY,
        published_at=POSTED,
        feed_entry_guid=source.feed_entry_guid if same_item else f"{SERIES} Episode 10",
    )
    repo = SimpleNamespace(
        load_discovery_candidate=lambda _: item,
        _supplement_same_work_official_images=lambda pkg, **_: pkg.images,
        prior_cover_image_fingerprints=lambda **_: [],
    )
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: profile)
    monkeypatch.setattr(cli, "immediate_news_action_expiry", lambda _: {"status": "not_applicable"})
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_a: 0)
    result, _ = cli.recollect_period_evidence(repo, episode)
    assert episode_evidence_missing(result) == (not same_item)
    assert "Series premiere" not in result.facts[0].statement
    assert result.sources[0].published_at == POSTED


def test_real_reader_note_and_repeated_title_are_returned_but_missing_platform_is_allowed(
    episode, monkeypatch
):
    episode.cards = episode.cards[:2]
    for card in episode.cards:
        card.title_lines = ["3기 9화 공개"]
        card.body_sentences = ["AnimeSchedule 페이지 제목에 3기 9화 공개 소식이 올라왔어."]
    episode.detail_body = (
        "출처 발췌문이나 공개 시점, 시청 경로, 회차 내용은 확인할 수 없어. "
        "그러니 실제 공개가 검증됐다고 단정하거나 어디서 볼 수 있는지 덧붙이진 않을게."
    )
    monkeypatch.setattr(
        reviewer,
        "AgentRepository",
        lambda: SimpleNamespace(persist_generated_content=lambda *a, **kw: None),
    )
    monkeypatch.setattr(
        reviewer,
        "ChatOpenAI",
        lambda **_: pytest.fail("Deterministic return should precede paid review"),
    )
    result = reviewer.review_content(
        {"package": episode.model_dump(mode="json"), "minimal_review": True}
    )
    reasons = {(i["code"], i["page"]) for i in result["qa_report"]}
    assert {("EDITORIAL_NOTE_IN_READER_COPY", None), ("CARD_REDUNDANT", 2)} <= reasons
    episode.detail_body = (
        "3기 9화 원어판 공개 알림이 올라왔어. 국내 방영 일정은 아직 발표되지 않았어."
    )
    episode.cards[1].body_sentences = ["이번에 공개 소식이 올라온 건 9화 원어판이야."]
    assert reviewer._reported_term_issues(episode) == []


def test_sparse_notice_writes_only_release_edition_and_labelled_feed_time(episode, monkeypatch):
    def no_authoring(*_a, **_kw):
        pytest.fail("The two-line bulletin does not need a model")

    for module in (generation, detail_writer):
        monkeypatch.setattr(module, "ChatOpenAI", no_authoring)
    monkeypatch.setattr(generation, "attach_menu_work_ids", lambda pkg: None)
    monkeypatch.setattr(
        generation,
        "AgentRepository",
        lambda: SimpleNamespace(
            prior_cover_image_fingerprints=lambda **kw: ["old-cover"],
            active_content_types=lambda: [{"code": "news"}],
        ),
    )

    def pick_images(pkg, *, limit):
        assert pkg.prior_image_fingerprints == ["old-cover"] and limit == 2
        return pkg.images[:2]

    monkeypatch.setattr(generation, "copy_image_pool", pick_images)
    deck = generation.generate_copy_deck(episode, requested_content_type="news")
    assert deck.usage.total_tokens == 0
    assert [c.title_lines for c in deck.deck.cards] == [
        ["전생 귀족, 감정 스킬로 성공하다", "3기 9화 공개"],
        ["원어판으로 나왔어"],
    ]
    assert {c.image_id for c in deck.deck.cards} == {i.id for i in episode.images[:2]}
    for base, card in zip(episode.cards[:2], deck.deck.cards, strict=True):
        base.title_lines, base.body_sentences, base.fact_ids = (
            card.title_lines,
            card.body_sentences,
            card.fact_ids,
        )
    episode.cards = episode.cards[:2]
    package = ContentPackage.model_validate(
        detail_writer.write_detail_body(
            {"package": episode.model_dump(mode="json"), "allow_llm": True}
        )["package"]
    )
    assert package.detail_body == (
        "〈전생 귀족, 감정 스킬로 성공하다〉 3기 9화가 원어판으로 공개됐어. "
        "공개 알림 게시 시각은 한국 시간 2026년 11월 23일 0시야."
    )
    assert package.detail_source_ids == ["anime_schedule_rss"] and package.detail_fact_ids == [
        "episode-item"
    ]
    assert not {i.code for i in check_detail_body(package)}
    assert reviewer._reported_term_issues(package) == []


def test_reviewer_catches_repeated_attribution_missing_content_inventory_and_inferred_language(
    episode,
):
    episode.cards[0].body_sentences = ["AnimeSchedule RSS가 3기 9화 공개 소식을 전했어."]
    episode.cards[1].body_sentences = ["이번에 나온 건 일본어판이야."]
    episode.detail_body = (
        "장면이나 줄거리, 한국에서 볼 수 있는 플랫폼 정보는 "
        "이 알림에 담겨 있지 않으니 덧붙이지 않을게."
    )
    issues = {(i.code, i.page) for i in reviewer._reported_term_issues(episode)}
    assert {
        ("EDITORIAL_NOTE_IN_READER_COPY", 1),
        ("EDITORIAL_NOTE_IN_READER_COPY", None),
        ("COPY_UNGROUNDED", 2),
    } <= issues
