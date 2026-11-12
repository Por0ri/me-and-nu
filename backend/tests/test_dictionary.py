"""정보사전 조회 규칙 (DB 없이 도는 부분)."""

import pytest

from app.integrations import dictionary
from app.integrations.dictionary import FactRow, Hit, Subject


def _hit(key, kind, entity_id, popularity=0.0, *, whole=True, parent_id=None, adult=False):
    return Hit(key=key, whole=whole, entity_id=entity_id, kind=kind, parent_id=parent_id,
               name=f"대상{entity_id}", popularity=popularity, adult=adult)


def test_name_key_ignores_spaces_symbols_and_case():
    assert dictionary.name_key("귀멸의 칼날: 무한열차편") == "귀멸의칼날무한열차편"
    assert dictionary.name_key("Monkey D. Luffy") == "monkeydluffy"


def test_strip_particle_keeps_short_words():
    assert dictionary.strip_particle("프리렌에서") == "프리렌"
    assert dictionary.strip_particle("나츠키가") == "나츠키"
    assert dictionary.strip_particle("하나") == "하나"


def test_question_spans_join_words_and_strip_particles():
    spans = dictionary.question_spans("장송의 프리렌에서 프리렌 성우 누구야?")
    assert spans["장송의프리렌"] == [(0, 2)]
    assert sorted(spans["프리렌"]) == [(1, 2), (2, 3)]
    assert "성우" not in spans  # 서비스 단어


def test_service_words_are_not_names():
    assert "애니" not in dictionary.question_spans("요즘 볼만한 애니 추천해줘")


def test_name_inside_longer_name_is_dropped():
    spans = dictionary.question_spans("하나에 나츠키가 맡은 캐릭터 알려줘")
    hits = [_hit("하나에나츠키", "person", 1, 9000), _hit("하나에", "person", 2, 9000)]
    assert [h.entity_id for h in dictionary.choose_targets(hits, spans)] == [1]


def test_short_or_partial_names_need_popularity():
    spans = dictionary.question_spans("엄마 탄지로 렌지")
    hits = [
        _hit("엄마", "character", 1, 0),  # 두 글자 + 인기 없음 → 버림
        _hit("탄지로", "character", 2, 17984, whole=False),  # 이름 일부 + 인기 → 씀
        _hit("렌지", "character", 3, 50, whole=False),  # 이름 일부 + 인기 낮음 → 버림
    ]
    assert [h.entity_id for h in dictionary.choose_targets(hits, spans)] == [2]


def test_partial_name_picks_most_popular_one():
    spans = dictionary.question_spans("고죠 생일")
    hits = [_hit("고죠", "character", 1, 3379, whole=False), _hit("고죠", "character", 2, 39207, whole=False)]
    assert [h.entity_id for h in dictionary.choose_targets(hits, spans)] == [2]


def test_season_is_merged_into_its_work_and_adult_is_dropped():
    spans = dictionary.question_spans("블루 록 2기")
    hits = [
        _hit("블루록", "entry", 10, 500, parent_id=99),
        _hit("블루록", "work", 99, 900),
        _hit("블루록", "entry", 11, 100, parent_id=77, adult=True),
    ]
    assert [h.entity_id for h in dictionary.choose_targets(hits, spans)] == [99]


def test_value_text_formats_dates_and_items():
    assert dictionary.value_text({"year": 2019, "month": 4, "day": 6}) == "2019년 4월 6일"
    assert dictionary.value_text({"month": 7, "day": 14}) == "7월 14일"
    assert dictionary.value_text({"en": "Netflix", "ko": "넷플릭스", "wikidata": "Q907311"}) == "넷플릭스"
    assert dictionary.value_text({"since": "2024-04-01", "title": "귀멸의 칼날", "service": "넷플릭스"}) == (
        "넷플릭스 (귀멸의 칼날, 2024-04-01부터)"
    )


def test_value_text_keeps_song_order_and_original_authors_only():
    song = {"type": "OP", "title": "紅蓮華", "singer": "LiSA", "sequence": 1}
    assert dictionary.value_text(song) == "OP1 · 제목 紅蓮華 · 가수 LiSA"
    source = {"format": "MANGA", "status": "FINISHED", "authors": [
        {"name": "Hajime Isayama", "role": "Story & Art"}, {"name": "Tatsuya Endou", "role": "Assistant"},
        {"name": "Ko Ransom", "role": "Translator (English)"}]}
    assert dictionary.value_text(source) == "만화 · 방영 끝 · 작가 Hajime Isayama (글 · 그림)"


def test_lines_show_agreement_and_conflict():
    subject = Subject(1, "entry", "귀멸의 칼날 · 鬼滅の刃 (TV, 2019)")
    anilist, wikidata, syoboi = ("AniList API", "https://anilist.co/anime/1"), ("위키데이터", "https://w/Q1"), (
        "しょぼいカレンダー", "https://cal/1")
    rows = [
        FactRow(1, "episodes", "26", *anilist),
        FactRow(1, "episodes", "26", *wikidata),
        FactRow(1, "start_date", "2019년 4월", *syoboi),
        FactRow(1, "start_date", "2019년 4월 6일", *anilist),
        FactRow(1, "duration_min", "24", *anilist),
        FactRow(1, "duration_min", "23", *wikidata),
    ]
    lines = dictionary.build_lines([subject], rows)
    texts = [line.text for line in lines]
    assert {line.subject for line in lines} == {"귀멸의 칼날 · 鬼滅の刃 (TV, 2019)"}
    assert "화수: 26 [AniList API · 위키데이터 2곳 일치]" in texts
    assert "방영 시작: 2019년 4월 6일 [AniList API · しょぼいカレンダー 2곳 일치]" in texts
    assert "한 화 길이(분): 24 [AniList API] / 23 [위키데이터] (출처마다 값이 다름)" in texts


def test_line_sources_keep_urls_once():
    subject = Subject(1, "character", "카마도 탄지로 (竈門炭治郎)")
    rows = [FactRow(1, "birthday", "7월 14일", "AniList API", "https://anilist.co/character/1"),
            FactRow(1, "gender", "남성", "AniList API", "https://anilist.co/character/1")]
    lines = dictionary.build_lines([subject], rows)
    assert lines[0].sources == (("AniList API", "https://anilist.co/character/1"),)


@pytest.mark.asyncio
async def test_lookup_is_empty_without_dictionary(monkeypatch):
    monkeypatch.setattr(dictionary.settings, "dict_database_url", None)
    result = await dictionary.lookup("귀멸의 칼날 감독 누구야?")
    assert result.targets == [] and result.lines == []


@pytest.mark.asyncio
async def test_lookup_failure_returns_empty(monkeypatch):
    class Broken:
        def connect(self):
            raise OSError("dictionary down")

    monkeypatch.setattr(dictionary, "_engine", lambda: Broken())
    result = await dictionary.lookup("귀멸의 칼날 감독 누구야?")
    assert result.targets == [] and result.lines == []


def test_core_fields_of_every_subject_come_before_other_fields(monkeypatch):
    monkeypatch.setattr(dictionary, "MAX_LINES", 3)
    first, second = Subject(1, "entry", "1기"), Subject(2, "entry", "2기")
    rows = [FactRow(1, "genre", "Action", "AniList API", None), FactRow(1, "episodes", "26", "AniList API", None),
            FactRow(2, "genre", "Action", "AniList API", None), FactRow(2, "episodes", "12", "AniList API", None)]
    lines = dictionary.build_lines([first, second], rows)
    assert [(line.subject, line.text.split(":")[0]) for line in lines] == [("1기", "화수"), ("1기", "장르"), ("2기", "화수")]


def test_wanted_fields_follow_question_words():
    director = dictionary.wanted_fields("귀멸의 칼날 감독 누구야?")
    assert "director" in director and "entry_count" in director and "theme_song" not in director
    assert "theme_song" in dictionary.wanted_fields("진격의 거인 OP 누가 불렀어?")
    assert "episodes" in dictionary.wanted_fields("그럼 몇 화까지 있어?")
    assert dictionary.wanted_fields("탄지로 멋있지 않아?") == dictionary.CORE_FIELDS
    assert "theme_song" not in dictionary.wanted_fields("Opus 라는 작품 어때?")  # 영어 낱말은 단어 전체만
