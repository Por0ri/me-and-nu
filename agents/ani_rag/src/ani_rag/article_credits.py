"""Local search hints from credits explicitly mentioned in a candidate article."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ani_rag.article_subject import ann_interview_subject
from ani_rag.models import RagContentType

if TYPE_CHECKING:
    from ani_rag.retrieval import EvidenceChunk


_CREDIT_ROLES = {
    "composer": r"\b(?:compos(?:er|ers|ed)|music(?:\s+(?:by|composition))?)\b|작곡|作曲",
    "performer": r"\b(?:perform(?:ed|er)|vocals?|sung|singer)\b|가수|보컬|歌唱",
    "theme": r"\b(?:opening|ending|theme song|theme|song|track)\b|주제가|오프닝|엔딩|主題歌",
    "director": r"\bdirect(?:or|ors|ed)\b|감독|監督",
    "producer": r"\b(?:CG\s+)?produc(?:er|ers|ed)\b|프로듀서|プロデューサー",
    "voice": r"\b(?:voice actor|voice actress|voiced)\b|성우|声優",
}
_PATTERNS = {key: re.compile(expr, re.I) for key, expr in _CREDIT_ROLES.items()}
_LINK = re.compile(r'\[([^\]\n]*)\]\(https?://(?:[^()\s"]|\([^()\s]*\))+(?:\s+"[^"\n]*")?\)')
_IMAGE = re.compile("!" + _LINK.pattern)
_NAMES = re.compile(
    r'«([^»\n]{2,90})»|["“「]([^"”」\n]{2,90})["”」]|'
    r"(?<!\w)([A-ZÀ-Þ][\w’'-]*(?: [A-ZÀ-Þ][\w’'-]*){1,3})(?!\w)"
)
_CREDIT_SEPARATOR = re.compile(
    r"\s*(?:(?:[:：=|,·・–—-]|by\b|is\b|was\b|the\b|named\b|titled\b|"
    r"은|는|을|를|이|가|の|は|により|担当)\s*)*", re.I,
)


def _search_text(text: str) -> str:
    # ANN and article bylines frequently romanize the same vowel with/without a macron.
    text = unicodedata.normalize("NFD", text.casefold())
    return unicodedata.normalize("NFC", re.sub(r"([a-z])[\u0300-\u036f]+", r"\1", text))


@dataclass(frozen=True)
class ArticleCredit:
    role: str
    name: str
    context: str
    ann_staff_url: str | None = None


def _headline_has_credit(title: str, role: str, name: str) -> bool:
    key = re.escape(_search_text(name))
    heading_credit = rf"(?:\b{role}[ ,]+{key}\b|\b{key},? (?:the )?{role}\b)"
    return bool(re.search(heading_credit, _search_text(title)))


def article_credits(content_type: RagContentType, body: str, *, title: str = "", url: str = "") -> tuple[ArticleCredit, ...]:
    if content_type is RagContentType.MUSIC:
        roles = ("composer", "performer", "theme")
    elif content_type is RagContentType.PEOPLE:
        roles = ("director", "producer", "voice")
    else:
        return ()
    interview = ann_interview_subject(title, url, body) if url and content_type is RagContentType.PEOPLE else None
    found: dict[tuple[str, str], ArticleCredit] = {}
    for line in body[:30_000].splitlines():
        line = _IMAGE.sub("", line).strip()
        if not line or line.startswith(("#", "![")):
            continue
        line = re.sub(r"<[^>]+>", " ", _LINK.sub(r"«\1»", line)).replace("*", "").replace("_", "")
        matches = [(role, match) for role in roles for match in _PATTERNS[role].finditer(line)]
        if not matches:
            continue
        for name_match in _NAMES.finditer(line):
            name = next(value for value in name_match.groups() if value).strip()
            # Title-cased job labels in headlines ("Director, CG Producer Streamed")
            # do not name a participant. Explicitly linked names remain available.
            if content_type is RagContentType.PEOPLE and name_match.group(3) and any(
                _PATTERNS[role].search(name) for role in roles
            ):
                continue
            for role, credit in matches:
                # 'director [Name]', 'composed by Name' and 'Name, the director'
                # are signals. Other nearby article links have no implied role.
                if credit.end() <= name_match.start():
                    between = line[credit.end():name_match.start()]
                elif name_match.end() <= credit.start():
                    between = line[name_match.end():credit.start()]
                    if not between.lstrip().startswith((",", "is ", "was ", "은", "는", "が", "は")):
                        continue
                else:
                    continue
                if len(between) > 32 or not _CREDIT_SEPARATOR.fullmatch(between):
                    continue
                start = min(credit.start(), name_match.start())
                end = max(credit.end(), name_match.end())
                words = [*line[max(0, start - 100):start].split()[-6:], line[start:end], *line[end:end + 80].split()[:4]]
                context = " ".join(words).replace("«", "").replace("»", "").strip()
                staff_url = interview[1] if interview and _headline_has_credit(title, role, name) else None
                found.setdefault((role, _search_text(name)), ArticleCredit(role, name, context, staff_url))
                if len(found) == 6:
                    return tuple(found.values())
    return tuple(found.values())


def chunk_credit_matches(credits: tuple[ArticleCredit, ...], text: str, *, source_url: str = "") -> int:
    """Count article credits with both a name and role nearby in this source chunk."""
    if not credits:
        return 0
    plain = _search_text(_LINK.sub(r"\1", text))
    count = 0
    for credit in credits:
        name = _search_text(credit.name)
        if credit.ann_staff_url:
            if source_url == credit.ann_staff_url and _ann_staff_lists_person(text, credit.role, name):
                count += 1
            continue
        # Bound Latin tokens so a short credit cannot match the prefix of another name.
        needle = re.escape(name)
        if name.isascii():
            needle = r"(?<!\w)" + needle + r"(?!\w)"
        for occurrence in re.finditer(needle, plain):
            before = plain[max(0, occurrence.start() - 45):occurrence.start()]
            after = plain[occurrence.end():occurrence.end() + 45]
            if _PATTERNS[credit.role].search(before) or _PATTERNS[credit.role].search(after):
                count += 1
                break
    return count


def _ann_staff_lists_person(text: str, role: str, name: str) -> bool:
    """Read a credited name under its own ANN staff heading, up to the next heading."""
    lines = [_search_text(line.strip()) for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.rstrip(":") != role or (not line.endswith(":") and lines[index + 1:index + 2] != [":"]):
            continue
        start = index + (1 if line.endswith(":") else 2)
        for pos in range(start, len(lines)):
            if ":" in lines[pos] or lines[pos + 1:pos + 2] == [":"]:
                break
            if lines[pos] == name:
                return True
    return False


def ann_interview_answer_hint(title: str, url: str, body: str, credits: tuple[ArticleCredit, ...],
                              references: Mapping[str, EvidenceChunk]) -> dict[str, object] | None:
    """Point the verifier to the printed first answer and that edition's independent staff entry."""
    subject = ann_interview_subject(title, url, body)
    if not subject:
        return None
    # A named answer must immediately follow the opening interview question. Later
    # unlabelled paragraphs cannot inherit the first speaker's name.
    section = re.split(r"(?m)^\s*(?:\* \* \*|---+)\s*$", body, maxsplit=1)[0]
    blocks = [part.strip() for part in re.split(r"\n\s*\n", section) if part.strip()]
    first = next((i for i, part in enumerate(blocks) if re.fullmatch(r"\*\*[^\n]+\?\*\*", part)), None)
    if first is None or first + 1 >= len(blocks):
        return None
    context = blocks[first + 1]
    named = re.fullmatch(r"\*\*([^:\n*]{2,80}):\*\*\s+([^\n]+)", context)
    if not named or len(context) > 1200:
        return None
    speaker = named[1]
    key = _search_text(speaker)
    if len(speaker.split()) < 2 or key not in _search_text(title):
        return None
    # A second printed speaker (bold or plain) makes this a different interview format.
    for part in blocks[first + 2:]:
        label = re.match(r"^(?:\*\*)?([\w’' -]{2,80}):(?:\*\*)?\s", part)
        if label and _search_text(label[1]) != key:
            return None
    for credit in credits:
        if _search_text(credit.name) != key or not _headline_has_credit(title, credit.role, speaker):
            continue
        matching = [ref for ref, chunk in references.items()
                    if chunk.source_id == "ann_encyclopedia" and chunk.source_url == subject[1]
                    and _ann_staff_lists_person(chunk.text[:1600], credit.role, key)]
        if matching:
            visible = _LINK.sub(r"\1", body).replace("**", "")
            credit_match = re.search(r"\b(" + re.escape(credit.role) + r") " + re.escape(speaker) + r"\b", visible, re.I)
            if not credit_match:
                return None
            question = re.split(r"(?<=[.!?])\s+", blocks[first].strip("*"))[-1]
            return {"speaker": speaker, "attribution_excerpt": context, "staff_credit_refs": matching,
                    "credit_passage": credit_match[0], "role": credit_match[1], "question": question}
    return None


def complete_named_interview_answer(claims: list, hint: dict) -> dict | None:
    """Anchor the verifier's exact opening answer to the printed turn and independently matched staff row."""
    speaker = hint["speaker"]
    context = hint["attribution_excerpt"]
    printed_answer = context.partition(":**")[2].strip()
    for row in claims:
        if not isinstance(row, dict) or row.get("status") != "attributed_quote":
            continue
        passage = row.get("claim")
        if not isinstance(passage, str) or passage.strip() != printed_answer:
            continue
        if not row.get("interview_answer") and (
            not re.search(r"\b(?:what|how|why|which)\b", hint["question"], re.I)
            or len(set(re.findall(r"[a-z]+", printed_answer.casefold()))) < 6
        ):
            continue  # A greeting or confirmation alone cannot supply the missing answer detail.
        supplied_name = row.get("speaker")
        if supplied_name and _search_text(str(supplied_name)) not in {_search_text(speaker), _search_text(speaker.split()[-1])}:
            continue
        row.update(speaker=speaker, attribution_excerpt=context, evidence_refs=hint["staff_credit_refs"])
        if not any(isinstance(r, dict) and r.get("status") == "supported" and
                   r.get("claim") == hint["credit_passage"] for r in claims):
            claims.append({"claim": hint["credit_passage"], "status": "supported", "role": "context",
                           "speaker": speaker, "attribution_excerpt": hint["role"],
                           "evidence_refs": hint["staff_credit_refs"]})
        return row
    return None
