"""Find the works a text names indirectly: a character, a theme song, or its artist.

Headlines often name a character ("ロック・リーのグッズが登場", "록 리 굿즈") or an artist
("SPYAIR 내한") instead of the anime. data/reference/work_characters.csv (Wikidata) and
data/reference/work_theme_songs.csv (AnimeThemes) map such names to menu_work_id; this module
only reports what that data says and never guesses. A name shared by several works, or too
short to be distinctive, is ignored, and a name must stand as its own word: "나미" inside
"쓰나미" or "リン" inside "リンゴ" is not a match. An artist who sang for several works points
to all of them, so callers should treat an artist-only match as weaker than a character or
song match.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The dictionaries are not committed (shared over Slack); ANI_MENU_WORK_MATCH_DIR points at
# wherever they were saved, e.g. model/catalog.
_REFERENCE = Path(
    os.environ.get("ANI_MENU_WORK_MATCH_DIR")
    or Path(__file__).resolve().parents[2] / "data" / "reference"
)
# Korean particles that may follow a name: "록리의 굿즈", "스칼렛이", "고죠에게".
_KOREAN_PARTICLES = (
    "에게서", "한테서", "이랑", "으로", "에게", "한테", "에서", "까지", "부터", "처럼",
    "의", "가", "이", "는", "은", "를", "을", "와", "과", "도", "로", "랑", "만", "님", "씨",
)
_SKIPPED = re.compile(r"[\s・·•.,'\"!?\-–—_:;/()（）「」『』【】\[\]《》〈〉＝=~〜]")
# Shortest name, per script of its first character, distinctive enough to match on its own.
_MIN_CHARS = {"latin": 7, "katakana": 4, "hiragana": 3, "han": 3, "hangul": 2, "other": 3}
_MIN_ARTIST_CHARS = 4
_MIN_SONG_CHARS = 6


@dataclass(frozen=True)
class NamedWorkMatch:
    menu_work_id: int
    kind: str  # "character", "theme_song", or "artist"
    name: str  # the name as it appears in the reference data


def _script(char: str) -> str:
    if not char:
        return ""
    if char.isascii() and char.isalnum():
        return "latin"
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3:
        return "hangul"
    if 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return "katakana"
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if "CJK UNIFIED" in unicodedata.name(char, ""):
        return "han"
    return "other"


def normalize(text: str) -> str:
    return _SKIPPED.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _indexed(text: str) -> tuple[str, list[int]]:
    """The normalized text and, for each of its characters, the position in ``text``."""
    folded = unicodedata.normalize("NFKC", text)
    chars: list[str] = []
    positions: list[int] = []
    for position, char in enumerate(folded):
        if _SKIPPED.match(char):
            continue
        for piece in char.casefold():
            chars.append(piece)
            positions.append(position)
    return "".join(chars), positions


def _stands_alone(original: str, start: int, end: int) -> bool:
    """Whether original[start:end] is a whole name rather than part of a longer word."""
    first, last = _script(original[start]), _script(original[end - 1])
    before = original[start - 1] if start > 0 else ""
    after = original[end] if end < len(original) else ""
    # A one-word romanized name ("Kage", "Rasa") is also an ordinary word; only a
    # capitalized one reads as a name.
    span = original[start:end]
    if first == "latin" and not any(char.isspace() for char in span) and span[0].islower():
        return False
    if before and _script(before) == first and first in {"latin", "katakana", "hangul"}:
        return False
    if not after or _script(after) != last:
        return True
    if last == "hangul":
        return original.startswith(_KOREAN_PARTICLES, end)
    return last not in {"latin", "katakana"}


def _distinctive(name: str, key: str) -> bool:
    """Whether a reference song title or artist-free name is safe to match on its own.

    Romanized titles collide with English words in title-case headlines, so they need
    several words. A short katakana name is often an ordinary loanword ("リード", "スピカ"),
    so it needs four characters unless it is a full name joined by a middle dot.
    """
    script = _script(key[0])
    if script == "katakana" and "・" in name and len(key) >= 3:
        return True
    if len(key) < _MIN_CHARS.get(script, 3):
        return False
    if script == "latin":
        words = name.split()
        return len(words) >= 2 and words[0].casefold() != "the"
    return True


def _rows(name: str) -> list[dict]:
    path = _REFERENCE / name
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _unique(index: dict[str, set[int]], spelling: dict[str, str]) -> dict[str, tuple[int, str]]:
    return {
        key: (next(iter(works)), spelling[key])
        for key, works in index.items()
        if len(works) == 1
    }


@lru_cache(maxsize=1)
def _character_index() -> dict[str, tuple[int, str]]:
    """Normalized character name -> (work, name), for names that belong to one work only."""
    index: dict[str, set[int]] = {}
    spelling: dict[str, str] = {}
    for row in _rows("work_characters.csv"):
        key = normalize(row["name"])
        # Romanized character names ("Episode", "Live", "Red Hood") collide with English
        # words; English headlines name the work itself anyway.
        if key and _script(key[0]) != "latin" and _distinctive(row["name"], key):
            index.setdefault(key, set()).add(int(row["menu_work_id"]))
            spelling.setdefault(key, row["name"])
    return _unique(index, spelling)


@lru_cache(maxsize=1)
def _song_indexes() -> tuple[dict[str, tuple[int, str]], dict[str, tuple[set[int], str]]]:
    """(song title -> one work, artist -> every work the artist sang for)."""
    songs: dict[str, set[int]] = {}
    song_spelling: dict[str, str] = {}
    artists: dict[str, set[int]] = {}
    artist_spelling: dict[str, str] = {}
    for row in _rows("work_theme_songs.csv"):
        work_id = int(row["menu_work_id"])
        title = normalize(row["song_title"])
        # Two-word romanized titles ("Every Day", "Hey! Smith!!") read as plain English.
        if (
            len(title) >= _MIN_SONG_CHARS
            and _distinctive(row["song_title"], title)
            and (_script(title[0]) != "latin" or len(row["song_title"].split()) >= 3)
        ):
            songs.setdefault(title, set()).add(work_id)
            song_spelling.setdefault(title, row["song_title"])
        for artist in (row.get("artists") or "").split(" / "):
            key = normalize(artist)
            if len(key) >= _MIN_ARTIST_CHARS:
                artists.setdefault(key, set()).add(work_id)
                artist_spelling.setdefault(key, artist.strip())
    return _unique(songs, song_spelling), {
        key: (works, artist_spelling[key]) for key, works in artists.items()
    }


def _spans(text: str, names: set[str], longest: int) -> list[tuple[int, str, str]]:
    """(position, key, original span) for every whole-word occurrence of a known name."""
    normalized, positions = _indexed(text)
    original = unicodedata.normalize("NFKC", text)
    found: list[tuple[int, str, str]] = []
    taken: set[int] = set()
    for size in range(min(longest, len(normalized)), 0, -1):
        for start in range(len(normalized) - size + 1):
            key = normalized[start : start + size]
            if key not in names or taken.intersection(range(start, start + size)):
                continue
            begin, end = positions[start], positions[start + size - 1] + 1
            if _stands_alone(original, begin, end):
                found.append((start, key, original[begin:end]))
                taken.update(range(start, start + size))
    return sorted(found)


def find_named_works(text: str) -> list[NamedWorkMatch]:
    """Works the text names through a character, a theme song title, or an artist."""
    characters = _character_index()
    songs, artists = _song_indexes()
    names = set(characters) | set(songs) | set(artists)
    if not names or not text:
        return []
    matches: list[NamedWorkMatch] = []
    for _, key, span in _spans(text, names, max(map(len, names))):
        if key in characters:
            work_id, name = characters[key]
            matches.append(NamedWorkMatch(work_id, "character", name))
        elif key in songs:
            work_id, name = songs[key]
            matches.append(NamedWorkMatch(work_id, "theme_song", name))
        else:
            works, name = artists[key]
            # Artist names are often single words ("LiSA", "Aimer"); require the exact casing
            # so an ordinary word in a sentence is not read as the artist.
            if re.sub(r"\s+", "", span) != re.sub(r"\s+", "", name):
                continue
            matches.extend(NamedWorkMatch(work_id, "artist", name) for work_id in sorted(works))
    return list(dict.fromkeys(matches))
