"""Extract the current video's own description and timed transcript from a Markdown watch page."""

import re
import unicodedata
from urllib.parse import parse_qs, urlsplit


def is_youtube_video(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.hostname == "youtu.be":
        return bool(re.fullmatch(r"/[\w-]+/?", parsed.path))
    if parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"}:
        return False
    return (parsed.path.rstrip("/") == "/watch" and bool(parse_qs(parsed.query).get("v"))) or bool(
        re.fullmatch(r"/(?:shorts|live|embed)/[\w-]+/?", parsed.path)
    )


_DATE = r"(?:[a-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}\s*[a-z]{3,9}\s+\d{1,2}|\d{4}\s*[년年./-]\s*\d{1,2}\s*[월月./-]\s*\d{1,2}\s*[일日.]?)"
_VIEWS_DATE = re.compile(rf"(?:[\d.,]+\s+views\s*[•·]\s*|조회수\s*[\d.,만천]+\s*회\s*[•·]?\s*|[\d.,万]+\s*回視聴\s*[•·]?\s*){_DATE}$", re.I)
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_END = re.compile(
    r"^(?:(?:…|\.{3})?\s*(?:more|더보기|もっと見る)|show (?:more|less|transcript).*|"
    r"follow along using the transcript\.?|(?:\d[\d.,]*\s+)?comments?.*|댓글.*|コメント.*|"
    r"up next|related videos|recommended(?: videos)?|video details|[•·]?watch full video|"
    r"reply|replies|view replies|(?:like|dislike)|subscribe[d]?|download|자막 표시|文字起こしを表示|"
    r"error\s+\d+.*|\*{0,2}\d{3}\.\*{0,2}\s+that.s an error.*|if playback doesn.t begin.*|"
    r"if you enjoy this video,.*|please visit our website.*)$", re.I,
)
_TIME = re.compile(r"^((?:\d+:)?\d{1,2}:[0-5]\d)(?:\s+(.+))?$")
_TRANSCRIPT = {"transcript", "스크립트", "文字起こし"}


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).replace("\\", "")


def _boundary(line: str) -> bool:
    return bool(_HEADING.match(line) or _END.fullmatch(line) or line.startswith(("![", "[![", "@"))
                or re.search(r"\]\(https?://(?:www\.)?(?:youtube\.com/(?:watch\?|shorts/)|youtu\.be/)", line))


def _timed_transcript(lines: list[str], start: int) -> str:
    segments = []
    last = -1
    index = start
    while index < len(lines):
        cue = _TIME.fullmatch(lines[index])
        if not cue:
            break
        seconds = 0
        for component in cue[1].split(":"):
            seconds = seconds * 60 + int(component)
        if seconds < last:
            break
        last = seconds
        index += 1
        text = cue[2]
        if not text and index < len(lines) and not _TIME.fullmatch(lines[index]):
            text = lines[index]
            index += 1
        if not text or _boundary(text) or text.startswith("["):
            break
        segments.append(text)
    return "\n".join(segments) if len(segments) >= 2 else ""


def youtube_video_body(title: str, markdown: str) -> str:
    title_key = _key(re.sub(r"\s+[-–|]\s*YouTube\s*$", "", title, flags=re.I))
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    descriptions = []
    transcripts = []
    for index, line in enumerate(lines):
        heading = _HEADING.fullmatch(line)
        if not heading or len(heading[1]) > 2 or _key(heading[2]) != title_key:
            continue
        # The same title occurs above the player, the actual description, and comments.
        # Only the description panel carries a description heading or the full view/date row.
        for pos in range(index + 1, min(index + 30, len(lines))):
            line = lines[pos]
            section = _HEADING.fullmatch(line)
            label = _key(section[2] if section else line)
            if label in _TRANSCRIPT:
                if value := _timed_transcript(lines, pos + 1):
                    transcripts.append(value)
                break
            if label in {"description", "설명", "概要欄"} or _VIEWS_DATE.fullmatch(line) or (
                pos >= index + 3 and re.fullmatch(_DATE, line, re.I)
                and re.fullmatch(r"[\d.,kmb万만]+\s*views", lines[pos - 1], re.I)
                and re.fullmatch(r"[\d.,kmb万만]+\s*likes", lines[pos - 2], re.I)
            ):
                description = []
                for tail_pos in range(pos + 1, len(lines)):
                    tail = lines[tail_pos]
                    tail_heading = _HEADING.fullmatch(tail)
                    if tail_heading and _key(tail_heading[2]) in _TRANSCRIPT:
                        if value := _timed_transcript(lines, tail_pos + 1):
                            transcripts.append(value)
                    if _boundary(tail) or tail.startswith("["):
                        break
                    description.append(tail)
                if description and _key(" ".join(description)) != title_key:
                    descriptions.append("\n".join(description))
            elif section or line.startswith(("@", "![")) or re.match(r"^(?:comments?\b|댓글|コメント|up next$)", line, re.I):
                break
    # YouTube renders the collapsed panel and its expanded copy in a single document.
    return "\n\n".join(filter(None, (max(descriptions, key=len, default=""), max(transcripts, key=len, default=""))))
