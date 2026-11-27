"""Explicit subject links on fetched editorial pages and work reference pages."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from ani_rag.interfaces import WorkIdentity

if TYPE_CHECKING:
    from ani_rag.catalog import LocalWorkCatalog


def _namu_article(url: str) -> str | None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "namu.wiki" or parsed.query
            or parsed.fragment or not parsed.path.startswith("/w/")):
        return None
    return unquote(parsed.path[3:])


def _namu_character_subject(title: str, url: str, body: str) -> str | None:
    document = title.removesuffix(" - 나무위키")
    subject, separator, category = document.rpartition("/")
    if not separator or not subject or category != "등장인물" or _namu_article(url) != document:
        return None
    heading = re.search(r"(?m)^# (.+?)\s*$", body[:12_000])
    if not heading:
        return None
    if heading[1] != document:
        link = re.fullmatch(r"\[([^\]\n]+)\]\(([^\s)]+)\)", heading[1])
        if not link or link[1] != document or _namu_article(link[2]) != document:
            return None
    header = body[heading.end():heading.end() + 7500].split("\n|", 1)[0]
    parent = re.search(r'상위 문서\s*:\s*\[([^\]\n]+)\]\(([^\s)]+)(?: +"[^"\n]*")?\)', header)
    if not parent or parent[1] != subject or _namu_article(parent[2]) != subject:
        return None
    anime = re.search(r"(?m)^- \[애니메이션/등장인물\]\(([^\s)]+)\)", header)
    if anime and _namu_article(anime[1]) == "분류:애니메이션/등장인물":
        return subject
    return None


def _soundtrack_subject(title: str, body: str) -> str | None:
    album = title.split(" | ", 1)[0]
    match = re.fullmatch(r'(.+?) Original Soundtrack', album, re.IGNORECASE)
    if not match:
        return None
    subject = match[1].strip('"“”')
    heading = re.search(r"(?m)^# " + re.escape(album) + r"[ \t]*$", body[:30_000])
    if not heading:
        return None
    # A genuine album description identifies both this record and the anime
    # it scores. Category tags, a composer biography and other albums do not.
    intro = body[heading.end():heading.end() + 8000].split("\n## ", 1)[0]
    lead = re.search(
        r"(?im)^[*_]*" + re.escape(album) + r"[*_]*(?: \([^\n)]{1,220}\))? is (?:"
        r"an album (?:containing|featuring) (?:background )?music (?:from|for)|"
        r"(?:an?|the) (?:original )?soundtrack(?: album)? (?:of|for)) the (?:TV )?anime "
        r"(?:series )?\[" + re.escape(subject) + r"\]\((https://[^\s)]+)(?: +\"[^\"\n]*\")?\)"
        r"(?=\s+(?:Season (?:One|1)\b)|[.,;]|$)", intro,
    )
    if not lead:
        return None
    target = urlsplit(lead[1])
    article = unquote(target.path).replace("_", " ").casefold()
    if ((target.hostname or "").endswith(".fandom.com") and not target.query and not target.fragment
            and article in {f"/wiki/{subject.casefold()} wiki", f"/wiki/{subject.casefold()}"}):
        return subject
    return None


_WIKI_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://(?:[^\s()]|\([^\s()]*\))+)(?: +"[^"\n]*")?\)')


def _catalog_wiki_host(catalog: LocalWorkCatalog | None, work: str, url: str, host_label: str) -> bool:
    if catalog is None:
        return False
    exact = catalog.resolve_sync(WorkIdentity((work,), {}, source_url=url))
    if len(exact.menu_work_ids) != 1:
        return False
    for title in catalog.unique_tv_entry_titles(exact.menu_work_ids[0]):
        name = _name_key(title)
        if not name.isascii():
            continue
        slug = re.sub(r"[^a-z0-9]", "", name)
        # Wiki subdomains can omit a long vowel, e.g. Kyoushitsu → Kyoshitsu.
        if host_label in (slug, slug.replace("ou", "o")):
            return True
    return False


@dataclass(frozen=True)
class ThemeSongArticle:
    work: str
    body: str


def _song_infobox_rows(intro: str, song: str) -> list[str]:
    fields = {"japanese name", "english name", "writer", "composer", "arranger", "artist", "release date", "anime debut"}
    for block in re.split(r"\n\s*\n", intro):
        rows = block.strip().splitlines()
        if not rows or rows[0].strip("| *_") != song or not all(row.startswith("|") for row in rows):
            continue
        selected = []
        for row in rows[1:]:
            cells = row.strip().strip("|").split("|")
            if len(cells) == 2 and cells[0].strip(" *_").casefold() in fields:
                selected.append(_WIKI_LINK.sub(r"\1", row).replace("*", ""))
        return selected
    return []


def fandom_theme_song(title: str, url: str, body: str, catalog: LocalWorkCatalog | None) -> ThemeSongArticle | None:
    page = re.fullmatch(r"([^|\n]+) \| ([^|\n]+) Wiki \| Fandom", title)
    if not page:
        return None
    song, work = page.groups()
    parsed = urlsplit(url)
    host = re.fullmatch(r"([a-z0-9-]+)\.fandom\.com", parsed.hostname or "")
    work_label = "".join(ch for ch in work.casefold() if ch.isalnum())
    if (parsed.scheme != "https" or parsed.netloc != parsed.hostname or parsed.query or parsed.fragment
            or not host or unquote(parsed.path).replace("_", " ") != "/wiki/" + song):
        return None
    heading = re.search(r"(?m)^# " + re.escape(song) + r"[ \t]*$", body[:30_000])
    if not heading:
        return None
    wiki_home = re.search(r"(?m)^\[" + re.escape(work) + r" Wiki\]\((https://[^\s)]+)\)[ \t]*$",
                          body[:heading.start()])
    if not wiki_home:
        return None
    home = urlsplit(wiki_home[1])
    if (home.netloc != parsed.netloc or home.query or home.fragment
            or unquote(home.path).replace("_", " ") != f"/wiki/{work} Wiki"):
        return None
    # Fandom puts edit controls between the H1 and the lead. Never search later
    # sections, recommendations or navigation tables for the work name.
    intro = body[heading.end():heading.end() + 7000].split("\n#", 1)[0]
    lead = next((p.strip() for p in re.split(r"\n\s*\n", intro)
                 if p.strip() and not p.lstrip().startswith(("- ", "!"))
                 and not all(line.lstrip().startswith("|") for line in p.splitlines())
                 and _WIKI_LINK.sub("", p).strip()), "")
    original_lead = lead
    # A translated wiki name needs both an independent catalog title for its host
    # and a link on the work name in the credit itself, never in the sidebar.
    host_label = host[1].replace("-", "")
    linked_works = [m for m in _WIKI_LINK.finditer(lead) if m[1] == work]
    subject = re.escape(work)
    if linked_works:
        if len(linked_works) != 1:
            return None
        link = linked_works[0]
        target_page = urlsplit(link[2])
        if (target_page.scheme != "https" or target_page.netloc != parsed.netloc
                or target_page.query or target_page.fragment
                or unquote(target_page.path).replace("_", " ") not in (f"/wiki/{work}", f"/wiki/{work} (anime)")):
            return None
        lead = lead[:link.start()] + "[" + work + "]" + lead[link.end():]
        subject = re.escape("[" + work + "]")
    if host_label != work_label and (not linked_works or not _catalog_wiki_host(catalog, work, url, host_label)):
        return None
    plain = _WIKI_LINK.sub(r"\1", lead).replace("*", "").replace("_", "")
    role = r"(?:opening|ending) theme(?: song)?"
    if len(re.findall(role, plain, re.I)) != 1:
        return None
    intro_song = re.escape(song) + r"(?:\s*\([^()\n]{1,220}\))?\s+"
    credit = r"(?:is|was) (?:used as )?(?:the |an? )?(?:(?:first|second|third|fourth) )?" + role
    prior = r"(?:is (?:a |the )?(?:single|song)\b[^.!?\n]{0,550}[.]\s+It )?"
    target = r" (?:for|of) (?:the )?" + subject + r" (?:TV )?anime(?: adaptation| series)?"
    ending = (r"(?:,\s*being featured (?:through|throughout) the (?:whole )?"
              r"(?:\d+(?:st|nd|rd|th)|first|second|third|fourth) season)?[.]?")
    singer_credit = r"(?:\s+(?:The (?:opening|ending|song)|It) (?:is|was) (?:sung|performed) by [^.!?\n]+[.]?)?"
    if not re.fullmatch(intro_song + prior + credit + target + ending + singer_credit, plain, re.I):
        return None
    visible_lead = _WIKI_LINK.sub(r"\1", original_lead).replace("*", "").replace("_", "")
    return ThemeSongArticle(work, "\n".join((visible_lead, *_song_infobox_rows(intro, song))))


def wiki_subject(title: str, url: str, body: str, *, catalog: LocalWorkCatalog | None = None) -> str | None:
    host = urlsplit(url).hostname or ""
    if host == "namu.wiki":
        return _namu_character_subject(title, url, body)
    if host.endswith(".fandom.com"):
        album = _soundtrack_subject(title, body)
        if album:
            return album
        song = fandom_theme_song(title, url, body, catalog)
        return song.work if song else None
    return None


_ANN_LINK = re.compile(r'\[([^\]\n]+)\]\((https://(?:www\.)?animenewsnetwork\.com/encyclopedia/'
                       r'(anime|people)\.php\?id=\d+)(?: +"[^"\n]*")?\)')
_ROLE = r"(?:producer|director|composer|screenwriter|animator|character designer)"


def _name_key(name: str) -> str:
    name = unicodedata.normalize("NFKD", name.casefold().replace("’", "'"))
    return "".join(c for c in name if not unicodedata.combining(c))


def ann_interview_subject(title: str, url: str, body: str) -> tuple[str, str] | None:
    """Recognize the linked subject and interviewee in the article's own lead."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"www.animenewsnetwork.com", "animenewsnetwork.com"}:
        return None
    heading = title.removesuffix(" - Anime News Network").strip()
    interview = re.fullmatch(r"(?:An? )?Interview(?:\s*:\s*| with\s+)(.+)", heading, re.IGNORECASE)
    if not interview:
        return None
    blocks = iter(part.strip() for part in re.split(r"\n\s*\n", body.strip()) if part.strip())
    lead = next(blocks, "")
    if lead.startswith("#"):
        if lead.lstrip("# ").strip("*_ ") != heading:
            return None
        lead = next(blocks, "")
    if re.match(r"^(?:written )?by\s", lead, re.IGNORECASE) and len(lead) < 200:
        lead = next(blocks, "")
    image = r'^(?:!\[[^\]]*\]\([^\s)]+(?: +"[^"\n]*")?\)\s*)+'
    lead = re.sub(image, "", lead)
    if not lead:
        lead = next(blocks, "")
    links = list(_ANN_LINK.finditer(lead[:2000]))
    if not links:
        return None
    linked = links[0]
    if linked[3] != "anime" or lead[:linked.start()].strip("*_ "):
        return None
    if not re.match(r"[*_]*\s+[A-Za-z]{2,}\s", lead[linked.end():]):
        return None
    label, ann_url = linked[1].strip("*_ "), linked[2]
    if any(other[3] == "anime" and other[2] != ann_url for other in links):
        return None
    works = "|".join(re.escape(_name_key(name)) for name in {label, label.split(":", 1)[0]})
    for person in (link for link in links if link[3] == "people"):
        role_match = re.search(r"\b(" + _ROLE + r")\s*[*_]*$", lead[:person.start()], re.IGNORECASE)
        if not role_match:
            continue
        work = r"(?:" + works + r")"
        name = re.escape(_name_key(person[1].strip("*_ ")))
        role = re.escape(_name_key(role_match[1]))
        if re.fullmatch(rf"(?:{work}(?:'s)? {role}[ ,]+{name}|{name},? (?:the )?{role} (?:of|for) {work})",
                        _name_key(interview[1])):
            return label, ann_url
    return None
