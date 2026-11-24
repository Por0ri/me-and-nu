import csv

import pytest

from ani_menu_agent import work_aliases
from ani_menu_agent.work_aliases import NamedWorkMatch, find_named_works

NARUTO, ONE_PIECE, AOT, GINTAMA, HAIKYU = 11, 8, 1, 30, 40


@pytest.fixture
def reference(tmp_path, monkeypatch):
    characters = [
        (NARUTO, "ロック・リー"),
        (NARUTO, "Rock Lee"),
        (419, "スライム"),
        (NARUTO, "록 리"),
        (ONE_PIECE, "ルフィ"),
        (ONE_PIECE, "Luffy"),
        (ONE_PIECE, "나미"),
        # A name two works share says nothing about which one is meant.
        (ONE_PIECE, "サンジ"),
        (HAIKYU, "サンジ"),
    ]
    songs = [
        (AOT, "OP1", "Guren no Yumiya", "Linked Horizon"),
        (GINTAMA, "OP13", "Sakura Mitsutsuki", "SPYAIR"),
        (HAIKYU, "OP4", "Imagination", "SPYAIR"),
        (AOT, "ED9", "Akuma no Ko", "Eve"),
    ]
    with (tmp_path / "work_characters.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["menu_work_id", "name"])
        writer.writerows(characters)
    with (tmp_path / "work_theme_songs.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["menu_work_id", "theme", "song_title", "artists"])
        writer.writerows(songs)
    monkeypatch.setattr(work_aliases, "_REFERENCE", tmp_path)
    work_aliases._character_index.cache_clear()
    work_aliases._song_indexes.cache_clear()
    yield
    work_aliases._character_index.cache_clear()
    work_aliases._song_indexes.cache_clear()


def works(text: str) -> set[tuple[int, str]]:
    return {(match.menu_work_id, match.kind) for match in find_named_works(text)}


def test_a_japanese_or_korean_character_name_points_to_its_work(reference) -> None:
    assert works("ロック・リーのグッズが登場") == {(NARUTO, "character")}
    assert works("록리의 굿즈가 나왔다") == {(NARUTO, "character")}
    assert works("록 리 굿즈 출시") == {(NARUTO, "character")}
    # Romanized character names collide with English words, so they are not used.
    assert works("New Rock Lee figure announced") == set()


def test_a_name_inside_a_longer_word_is_not_a_match(reference) -> None:
    assert works("쓰나미 경보") == set()
    assert works("ルフィーナの新作") == set()
    assert works("나미비아 여행") == set()
    assert works("나미가 등장") == {(ONE_PIECE, "character")}
    assert works("限定の「スライムまん」") == set()
    assert works("新商品「ロック・リーもち」") == set()
    assert works("新商品「ロック・リー饅頭」") == set()


def test_a_short_katakana_name_needs_a_full_name_form(reference) -> None:
    # "ルフィ" alone is three katakana, as short as ordinary loanwords like "リード".
    assert works("ルフィの新作フィギュア") == set()
    assert works("ロック・リーの新作フィギュア") == {(NARUTO, "character")}


def test_goods_headline_identity_uses_the_named_work_or_the_character_subject(reference):
    from ani_menu_agent.work_catalog import resolve_work_matches

    def matches(title, **kwargs):
        return [
            (e.menu_work_id, e.basis)
            for e in resolve_work_matches(titles=[title], content_type="goods", **kwargs)
        ]

    assert matches("『未登録のゲーム』とコラボ！「ロック・リー」の特典も") == []
    assert matches("新しい「スライム」のグッズが登場") == []
    assert matches("『ロック・リー』のグッズ") == [(NARUTO, "headline_character")]
    assert matches("「ロック・リー」のグッズ") == [(NARUTO, "headline_character")]
    assert matches("『ラグナクリムゾン』の「スライム」のグッズ") == [(419, "article_title")]
    assert matches("『未登録のゲーム』の特典も", structured_references=[
        {"relation": "about", "name": "Naruto"}
    ]) == [(NARUTO, "structured_about")]


def test_period_package_keeps_character_hint_out_of_art_and_generation_until_confirmed(reference):
    from ani_menu_agent.models import ContentPackage, SourceEvidence, WorkMatchEvidence
    from ani_menu_agent.work_catalog import UnconfirmedPeriodWorkError, attach_menu_work_ids, resolve_menu_work_ids

    headline = "ロック・リーの新作フィギュア"
    source = SourceEvidence(id="newsroom", publisher="Newsroom", title=headline, url="https://example.org/goods/lee")
    package = ContentPackage(request_id="rock-lee-goods", topic=headline, content_kind="information",
                             content_type="goods", sources=[source])
    assert resolve_menu_work_ids(package) == []
    package.menu_work_ids = [NARUTO]
    package.work_match_evidence = [WorkMatchEvidence(menu_work_id=NARUTO, basis="headline_character",
                                                    matched_value="ロック・リー", confidence=0.92)]
    with pytest.raises(UnconfirmedPeriodWorkError):
        attach_menu_work_ids(package, check_period_artwork=True)
    package.work_match_evidence = [WorkMatchEvidence(menu_work_id=NARUTO, basis="llm_body_context",
                                                    matched_value="NARUTO", evidence_url=source.url, confidence=0.95)]
    assert attach_menu_work_ids(package, check_period_artwork=True).menu_work_ids == [NARUTO]


def test_inside_games_product_name_cannot_restore_a_legacy_work_or_its_art(reference, monkeypatch):
    from ani_menu_agent import cli
    from ani_menu_agent.models import ContentPackage, SourceEvidence, WorkMatchEvidence
    from ani_menu_agent.storage import AgentRepository
    from ani_menu_agent.work_catalog import resolve_menu_work_ids, resolve_work_matches

    title = ("ファミマで『ドラゴンクエストモンスターズ4』コラボ開催！「スライムまん」や限定アクキー配布、"
             "くじやカード付きチップスなど販売も | インサイド")
    url = "https://www.inside-games.jp/article/2026/11/24/191443.html"
    source = next(s for s in cli.load_source_registry().sources if s.id == "inside_goods")
    assert cli._candidate_menu_work_ids({"title": title, "url": url}, source, "goods") == []
    assert resolve_work_matches(titles=[title], source_ids=[source.id], source_url=url, content_type="goods") == []
    evidence = SourceEvidence(id=source.id, title=title, url=url, publisher=source.name, role=source.role)
    legacy = ContentPackage(request_id="old-goods", topic=title, content_kind="information",
                            content_type="goods", sources=[evidence], menu_work_ids=[419])
    legacy.work_match_evidence = [
        WorkMatchEvidence(menu_work_id=419, basis="headline_character", matched_value="スライム", confidence=0.92)
    ]
    later = legacy.model_copy(deep=True)
    assert resolve_menu_work_ids(legacy) == []
    monkeypatch.setattr(cli, "collect_kitsu_work_images", lambda *_a, **_k: pytest.fail("Unrelated artwork fetch"))
    assert cli._add_same_work_catalog_images(legacy, [source], 20) == 0
    repo = object.__new__(AgentRepository)
    monkeypatch.setattr(repo, "active_content_types", lambda: [{"code": "goods"}])
    monkeypatch.setattr(repo, "connect", lambda: pytest.fail("Unconfirmed identity reached feed write"))
    with pytest.raises(ValueError, match="기존 작품 ID"):
        repo.enqueue_for_feed(later)


def test_a_name_two_works_share_is_ignored(reference) -> None:
    assert works("サンジの誕生日") == set()


def test_songs_point_to_one_work_and_artists_to_every_work_they_sang_for(reference) -> None:
    assert works("Linked Horizon performs Guren no Yumiya live") == {
        (AOT, "theme_song"),
        (AOT, "artist"),
    }
    assert works("SPY AIR 내한 공연 확정") == {(GINTAMA, "artist"), (HAIKYU, "artist")}
    # An artist name must match its exact casing, and very short names are skipped.
    assert works("Christmas Eve live") == set()
    assert works("Spyair fans") == set()
    assert find_named_works("Linked Horizon 새 앨범") == [
        NamedWorkMatch(AOT, "artist", "Linked Horizon")
    ]


def test_missing_reference_files_find_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(work_aliases, "_REFERENCE", tmp_path)
    work_aliases._character_index.cache_clear()
    work_aliases._song_indexes.cache_clear()

    assert find_named_works("ロック・リーのグッズ") == []
