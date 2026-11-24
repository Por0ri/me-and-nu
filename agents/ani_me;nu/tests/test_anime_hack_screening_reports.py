from datetime import date

import pytest

from ani_menu_agent.source_freshness import expiry_evidence
from ani_menu_agent.sources import page_collector
from ani_menu_agent.sources.registry import load_source_registry

SOURCE = next(s for s in load_source_registry().sources if s.id == "anime_hack_events")
FATE_TITLE = "キャストが「Fate」一挙上映イベントに登壇"
FATE_LEAD = (
    "劇場版の公開を記念し、11月8日に東京・TOHOシネマズ新宿で、"
    "全3章の一挙上映＆トークイベントが開催された。"
)
NAGOYA_TITLE = "神山監督、特集上映で過去作制作秘話語る"
NAGOYA_LEAD = (
    "名古屋市で開催中の第2回「名古屋映画祭」で、11月14日のオールナイト上映"
    "【神山監督特集】に監督が登壇し、作品の見どころを語った。"
)


def capture(monkeypatch, title, lead, extra="", day=20, url="https://anime.eiga.com/news/127916/"):
    html = (
        f'<meta property="article:published_time" content="2026-11-{day:02d}">'
        f"<title>{title}</title><main><h1>{title}</h1>"
        f'<div class="articleContainer"><div class="newsDetailBox"><p>{lead}</p>{extra}'
        "</div></div></main>"
    )
    monkeypatch.setattr(page_collector, "_get_html", lambda *_: (html, url))
    return page_collector.collect_official_page(SOURCE, url, content_type="event").model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    ("article", "title", "lead", "rest", "published", "occurred"),
    [
        ("127916", FATE_TITLE, FATE_LEAD, "<p>映画は11月20日に公開。</p>", 20, "2026-11-08"),
        (
            "127879",
            NAGOYA_TITLE,
            NAGOYA_LEAD,
            "<p>第2回「名古屋映画祭」（通称ANIAFF）は、11月18日まで名古屋市で開催。</p>",
            18,
            "2026-11-14",
        ),
    ],
)
def test_reported_occurrence_is_expired_without_deriving_an_end_date(
    monkeypatch,
    article,
    title,
    lead,
    rest,
    published,
    occurred,
):
    record = capture(
        monkeypatch, title, lead, rest, published, f"https://anime.eiga.com/news/{article}/"
    )
    decision = expiry_evidence(record, "event", today=date(2026, 11, 24))
    assert decision["status"] == "expired" and decision["occurred_on"] == occurred
    assert lead.rstrip("。") in decision["evidence"]
    assert decision["end_date"] is None and record["event_end"] is None


@pytest.mark.parametrize(
    ("title", "lead", "rest"),
    [
        (FATE_TITLE, FATE_LEAD.replace("開催された", "開催される"), ""),
        (FATE_TITLE, FATE_LEAD, "<section>12月にアンコール上映を予定。</section>"),
        (FATE_TITLE, FATE_LEAD, "<section>次の回は11月28日の夜。</section>"),
        (FATE_TITLE, FATE_LEAD.replace("11月8日", "昨年11月8日"), ""),
        (FATE_TITLE, FATE_LEAD.replace("11月8日", "11月28日"), ""),
        (
            FATE_TITLE,
            FATE_LEAD.replace("11月8日に東京", "11月8日に発表された計画によると、東京"),
            "",
        ),
        (NAGOYA_TITLE, NAGOYA_LEAD, "<p>映画祭は現在も開催中。</p>"),
        ("今後の上映スケジュール", FATE_LEAD, ""),
    ],
)
def test_future_mixed_or_uncertain_reports_do_not_expire_as_completed(
    monkeypatch, title, lead, rest
):
    record = capture(monkeypatch, title, lead, rest)
    assert record["completed_screening"] is None
    assert expiry_evidence(record, "event", today=date(2026, 11, 24))["status"] == "unresolved"
