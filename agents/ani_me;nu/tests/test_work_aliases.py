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


def test_a_short_katakana_name_needs_a_full_name_form(reference) -> None:
    # "ルフィ" alone is three katakana, as short as ordinary loanwords like "リード".
    assert works("ルフィの新作フィギュア") == set()
    assert works("ロック・リーの新作フィギュア") == {(NARUTO, "character")}


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
