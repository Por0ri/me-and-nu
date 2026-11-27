"""Locate numbered TV-episode material without treating links or episode counts as recaps."""

from __future__ import annotations

import re


EpisodeTarget = tuple[int | None, int]
_EP = re.compile(r"\b(?:episode|ep\.?)\s*#?\s*(\d{1,4})\b|(?<!\d)(?:제|第)?\s*(\d{1,4})\s*(?:화|回|話)(?!\w)", re.I)
_SEASON = re.compile(r"\bseason\s*(\d{1,2})\b|시즌\s*(\d{1,2})|第\s*(\d{1,2})\s*期|(\d{1,2})\s*(?:기|期)\b", re.I)
_GUIDE = re.compile(r"episodes?\s+(?:guide|list)|list of (?:.{1,90} )?episodes\b|(?:회차|에피소드)\s*(?:가이드|목록|정보)|各話(?:紹介|あらすじ)|エピソード(?:ガイド|一覧)", re.I)
_LINK = re.compile(r"!?\[([^\]\n]*)\]\(https?://[^\s]+?(?:\s+[\"'][^\"'\n]*[\"'])?\)(?=\s|$|[.,;:(|])")
_NON_TV = re.compile(r"\b(?:movie|film|ova|oad|trailer|soundtrack)\b|극장판|劇場版", re.I)


def is_episode_guide_title(title: str) -> bool:
    return bool(_episode(title) or _GUIDE.search(title))


def _episode(text: str) -> re.Match | None:
    return next((m for m in _EP.finditer(text)
                 if not re.search(r"(?:총|전체|전|全)\s*$", text[:m.start()])), None)


def _number(match: re.Match) -> int:
    return int(next(group for group in match.groups() if group is not None))


def episode_targets(title: str, body: str) -> tuple[EpisodeTarget, ...]:
    """Use an episode headline, or numbered entries in an actual episode list."""
    if not is_episode_guide_title(title):
        return ()
    season_match = _SEASON.search(title)
    season = _number(season_match) if season_match else None
    if match := _episode(title):
        return ((season, _number(match)),)
    targets: dict[EpisodeTarget, None] = {}
    for line in body.splitlines():
        if line.startswith("#"):
            if match := _SEASON.search(_LINK.sub(r"\1", line)):
                season = _number(match)
        # Require an entry marker; prose can mention unrelated episodes in passing.
        label = line.lstrip(" \t#*-|")
        if match := _EP.match(label):
            targets[season, _number(match)] = None
    return tuple(targets)


def episode_title_matches(targets: tuple[EpisodeTarget, ...], title: str) -> bool:
    match = _episode(title)
    if not match or _NON_TV.search(title):
        return False
    season = _SEASON.search(title)
    return (_number(season) if season else None, _number(match)) in targets


def episode_reference_matches(targets: tuple[EpisodeTarget, ...], title: str, text: str) -> bool:
    """A matching episode label must be accompanied by actual non-link source text."""
    if not targets or _NON_TV.search(title):
        return False
    season_match = _SEASON.search(title)
    season = _number(season_match) if season_match else None
    title_episode = _episode(title)
    if season is not None and not any(s == season for s, _ in targets):
        return False
    if title_episode and not any(n == _number(title_episode) and (season is None or s == season) for s, n in targets):
        return False
    fact_text = _LINK.sub(" ", text)
    lines = [line.strip(" *#-\t|") for line in fact_text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if not season_match and (m := _SEASON.search(line)) and (len(line) < 35 or _EP.search(line)):
            season = _number(m)
        match = _episode(line)
        if not match and not title_episode:
            continue
        number = _number(title_episode or match)
        if (season, number) not in targets:
            continue
        detail = line[match.end():] if match else line
        if idx + 1 < len(lines) and not _EP.search(lines[idx + 1]) and not _SEASON.search(lines[idx + 1]):
            detail += " " + lines[idx + 1]
        # Dates, numeric ratings and a table of linked reviews are not episode descriptions.
        if len(re.findall(r"[^\W\d_]", detail)) >= 22 and not re.search(r"https?://", detail):
            return True
    return False
