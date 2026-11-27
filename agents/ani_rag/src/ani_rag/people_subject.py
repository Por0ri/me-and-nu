"""Source-backed human subjects for people articles, including attributed interviews."""

from collections.abc import Mapping, Sequence
import re
from urllib.parse import urlsplit

_ROLES = {
    "director": r"\bdirect(?:or|ors|ed|ing|ion)\b|감독|연출|監督|演出",
    "voice": r"\b(?:voic(?:e|ed|ing)|dubb(?:ed|ing)|voice\s+act(?:or|ress)|portrayed)\b|성우|목소리|더빙|声優|声を|声の|吹替",
    "producer": r"\bproduc(?:er|ers|ed|ing|tion)\b|프로듀|제작|プロデュー|製作|制作",
    "writer": r"\b(?:creat(?:or|ed)|original (?:author|story)|author|wrote|written|writer|script|screenplay|composition)\b|작가|원작|각본|집필|原作|脚本|構成",
    "designer": r"\bdesign(?:er|ed|ing)?\b|디자인|디자이너|デザイン",
    "music": r"\b(?:music|compos(?:ed|er|ing)|sang|singer|performed|sung|vocals?)\b|음악|작곡|노래|보컬|音楽|作曲|歌唱",
    "animation": r"\b(?:animator|animated|drawing|drew|illustrated|storyboard(?:ed)?)\b|작화|원화|애니메이터|그렸|그린|作画|原画|絵コンテ",
    "interview": r"\b(?:said|told|answered|interview(?:ed|ee)?|explained|recalled)\b|인터뷰|답했|답변|밝혔|語っ|答え|インタビュー",
}
_ROLE_PATTERNS = {role: re.compile(pattern, re.I) for role, pattern in _ROLES.items()}
_ORGANIZATION = re.compile(r"\b(?:animation|studio[s]?|pictures|productions|committee|entertainment|inc|corp)\b|주식회사|스튜디오|위원회|株式会社|委員会", re.I)
_ACTIVE = re.compile(r"\b(?:directed|voiced|produced|composed|created|wrote|played|dubbed|designed|animated)\b", re.I)
_LATIN_NAME = r"[A-ZÀ-Þ][\w’'-]*(?:[ \t]+[A-ZÀ-Þ][\w’'-]*){1,3}"
_JOB = r"(?:voice actor|voice actress|CG producer|character designer|manga creator|original author|music composer|director|producer|composer|writer|animator)"
_ENGLISH_CREDITS = [
    re.compile(rf"(?i:\b(?P<role>{_JOB})\b)\s*[:：]?\s+(?P<name>{_LATIN_NAME})"),
    re.compile(rf"(?P<name>{_LATIN_NAME}),?\s+(?i:(?:is|was|served as) (?:the |a )?(?P<role>{_JOB})\b)"),
    re.compile(rf"(?P<name>{_LATIN_NAME})\s+(?P<role>(?i:{_ACTIVE.pattern}))"),
    re.compile(rf"(?P<role>(?i:{_ACTIVE.pattern}))\s+(?i:by)\s+(?P<name>{_LATIN_NAME})"),
]


def credit_roles(text: str) -> set[str]:
    return {role for role, pattern in _ROLE_PATTERNS.items() if pattern.search(text)}


def supported_credit(claim: str, name: object, relation: object) -> bool:
    if not credit_span(claim, name, relation) or _ORGANIZATION.search(name) or not credit_roles(relation):
        return False
    if name in relation or relation in name:
        return False
    names = tuple(re.finditer(re.escape(name), claim))
    roles = re.finditer(re.escape(relation), claim)
    return any(_credit_neighbors(claim, person, role) for role in roles for person in names)


def _credit_neighbors(claim: str, person: re.Match, role: re.Match) -> bool:
    relation = role.group()
    if role.end() <= person.start():
        gap = claim[role.end():person.start()]
        if _ACTIVE.search(relation) and not re.search(r"\bby\s*$", relation + gap, re.I):
            return False
    elif person.end() <= role.start():
        gap = claim[person.end():role.start()]
        if _ACTIVE.search(relation) and re.search(r"\b(?:was|is|were|been|by)\b", gap + relation, re.I):
            return False
    else:
        return False
    if re.search(r"[.;!?。！？\n]", gap):
        return False
    if re.search(r"[A-Za-z]", relation):
        return bool(re.fullmatch(r"[\s:|,–—-]*(?:(?:as|by|is|was|a|an|the|served)\s*)*[\s:|,–—-]*", gap, re.I))
    return bool(re.fullmatch(r"[\s:：,・–—-]*(?:(?:은|는|이|가|을|를|의|로|역을|주연을|は|が|の|を|役|担当)\s*)*[\s:：,・–—-]*", gap))


def ann_people_credit_body(url: str, body: str) -> str | None:
    """ANN places staff/cast tables after release listings; preserve their own cells and section labels."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"animenewsnetwork.com", "www.animenewsnetwork.com"} or parsed.path != "/encyclopedia/anime.php":
        return None
    lines = body.splitlines()
    sections = []
    for pos, line in enumerate(lines[:-1]):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        labels = [(index, cell) for index, cell in enumerate(cells)
                  if re.fullmatch(r"(?:[A-Za-z]+(?:[- ][A-Za-z]+){0,2}) (?:staff|cast)", cell, re.I)]
        if not labels or not lines[pos + 1].startswith("|"):
            continue
        row = lines[pos + 1].strip().strip("|").split("|")
        for index, label in labels:
            if index >= len(row) or "/encyclopedia/people.php?id=" not in row[index]:
                continue
            cell = re.sub(r"!\[[^\]]*\]\([^)\s]+\)", "", row[index])
            cell = re.sub(r"\[([^\]\n]+)\]\(https?://[^()\s]+\)", r"\1", cell)
            cell = re.sub(r"<br\s*/?>", "\n", cell, flags=re.I).replace("**", "").strip()
            sections.append(f"## {label}\n{cell[:7500]}")
    if not sections:
        return None
    heading = next((line for line in lines if line.startswith("# ")), "")
    return "\n\n".join(filter(None, (heading, *sections)))[:30_000]


def credit_span(claim: str, name: object, relation: object) -> bool:
    """Both original-language spans must occur in the verified passage."""
    return (isinstance(name, str) and isinstance(relation, str)
            and bool(name.strip() and relation.strip()) and name.strip() != relation.strip()
            and name.strip() in claim and relation.strip() in claim)


def people_subjects(claims: Sequence[Mapping[str, object]]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in claims:
        if not isinstance(item, Mapping):
            continue
        passage = str(item.get("claim") or "")
        if item.get("kind") == "attributed_quote" and item.get("speaker") and item.get("attribution_excerpt"):
            result.append((str(item["speaker"]), passage))
            continue
        credit = item.get("people_credit")
        if isinstance(credit, Mapping) and supported_credit(passage, credit.get("name"), credit.get("relation")):
            result.append((str(credit["name"]), passage))
            continue
        # A verified passage can itself give an explicit credit even if the
        # assessor omitted its optional attribution or the job predates it.
        plain = re.sub(r"\[([^\]\n]+)\]\(https?://[^()\s]+\)", r"\1", passage)
        for pattern in _ENGLISH_CREDITS:
            for match in pattern.finditer(plain):
                name, role = match["name"], match["role"]
                if not credit_roles(name) and supported_credit(plain, name, role):
                    result.append((name, passage))
    return tuple(dict.fromkeys(result))
