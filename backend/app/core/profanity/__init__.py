"""욕설 · 선정적인 말 거르기 (2026-11-25 사용자 결정).

닉네임 (가입 · 프로필 수정 · 닉네임 확인) 과 토론 주제 (제목 · 설명 · 선택지) 는 막고,
토론 채팅은 걸린 낱말만 별표로 가려서 보낸다.

- 목록 words_ko.txt: badwords-ko (MIT, LICENSE-badwords-ko.txt) 를 바탕으로 흔한 말과 겹치는 낱말을 빼고 선정적인 말을 더했다.
- 예외 allow_ko.txt: 걸리는 낱말을 품은 멀쩡한 말 (시발점, 자위대 …). 그 말 안에서 걸린 건 넘긴다.

검사 방법
- 소문자로 바꾸고 한글 · 자모 · 영문 · 숫자만 남겨 비교한다. 그래서 '씨.발' · '씨 발' 처럼 사이에 기호나 띄어쓰기를 넣어도 잡힌다.
- 띄어쓰기에 걸쳐 잡힌 건 앞 낱말의 처음부터 시작할 때만 인정한다. '위험한 녀석' 처럼 두 낱말의 끝과 처음이 붙어
  우연히 생긴 건 넘긴다 ('씨 발' 처럼 글자를 띄워 쓴 건 앞 낱말의 처음부터라 잡힌다).
- 걸린 자리를 원문 위치로 돌려준다 (채팅 가리기용). 위치는 NFC 로 맞춘 글 기준이다.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_KEEP = re.compile(r"[가-힣ㄱ-ㅣa-z0-9]")


def _compact(text: str) -> tuple[str, list[int]]:
    """비교용 글 (소문자, 한글 · 자모 · 영문 · 숫자만) 과 그 글자마다의 위치. text 는 NFC 로 맞춘 글이어야 한다."""
    chars: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        lowered = char.lower()
        if len(lowered) == 1 and _KEEP.match(lowered):
            chars.append(lowered)
            index.append(position)
    return "".join(chars), index


def _read(name: str) -> list[str]:
    lines = (_HERE / name).read_text(encoding="utf-8").splitlines()
    return [unicodedata.normalize("NFC", line.strip()) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _lookahead(words: set[str]) -> re.Pattern[str] | None:
    # 자리마다 가장 긴 낱말 하나를 잡는다 (앞을 보기만 해서 겹치는 낱말도 모두 찾는다)
    if not words:
        return None
    return re.compile("(?=(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + "))")


@lru_cache(maxsize=1)
def _rules() -> tuple[re.Pattern[str] | None, re.Pattern[str] | None, tuple[str, ...]]:
    words: set[str] = set()
    symbols: set[str] = set()
    for word in _read("words_ko.txt"):
        compact, _ = _compact(word)
        if len(compact) >= 2:
            words.add(compact)
        elif not compact:
            symbols.add(word)  # 글자 없이 기호만인 낱말 (凸 같은 것) 은 원문에서 그대로 찾는다
        # 한 글자로 줄어드는 낱말은 멀쩡한 글에 너무 많이 걸려서 쓰지 않는다
    allows = {compact for compact, _ in map(_compact, _read("allow_ko.txt")) if compact}
    return _lookahead(words), _lookahead(allows), tuple(sorted(symbols))


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _starts_word(text: str, start: int) -> bool:
    if start == 0:
        return True
    before = text[start - 1].lower()
    return not (len(before) == 1 and _KEEP.match(before))


def banned_spans(text: str) -> list[tuple[int, int]]:
    """걸린 낱말의 위치 [시작, 끝) 목록 (NFC 로 맞춘 글 기준, 겹치거나 붙은 건 합침)."""
    words, allows, symbols = _rules()
    normalized = unicodedata.normalize("NFC", text)
    compact, index = _compact(normalized)
    safe = [False] * len(compact)
    if allows is not None:
        for match in allows.finditer(compact):
            for position in range(match.start(), match.start() + len(match.group(1))):
                safe[position] = True
    spans: list[tuple[int, int]] = []
    if words is not None:
        for match in words.finditer(compact):
            first, last = match.start(), match.start() + len(match.group(1)) - 1
            if all(safe[first:last + 1]):
                continue
            start, end = index[first], index[last] + 1
            if any(char.isspace() for char in normalized[start:end]) and not _starts_word(normalized, start):
                continue
            spans.append((start, end))
    for symbol in symbols:
        position = normalized.find(symbol)
        while position != -1:
            spans.append((position, position + len(symbol)))
            position = normalized.find(symbol, position + 1)
    return _merge(spans)


def has_banned(text: str) -> bool:
    return bool(banned_spans(text))


def mask_banned(text: str) -> str:
    """걸린 낱말만 같은 길이의 '*' 로 가린 글. 걸린 게 없으면 받은 그대로 돌려준다.
    길이를 그대로 두어서 채팅 글자 수 한도 (300자) 를 넘지 않는다."""
    spans = banned_spans(text)
    if not spans:
        return text
    normalized = unicodedata.normalize("NFC", text)
    parts: list[str] = []
    last = 0
    for start, end in spans:
        parts.append(normalized[last:start])
        parts.append("*" * (end - start))
        last = end
    parts.append(normalized[last:])
    return "".join(parts)
