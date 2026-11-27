"""Keep a web interview tied to a participant's answer through assessment and writing."""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit


_INTERVIEW = re.compile(r"\binterviews?\b|인터뷰|インタビュー", re.I)
_INTERVIEW_LABEL = re.compile(
    r"^(?:(?:exclusive|an?)\s+)?interview(?:\s*[:：–—]|\s+with\b)|"
    r"^[\[(【](?:exclusive\s+)?(?:interview|인터뷰|インタビュー)[\])】]|^(?:인터뷰|インタビュー)\s*[:：]", re.I,
)
_PREFERENCE = re.compile(r"\bfavou?rites?\b|최애|가장 좋아|お気に入り|一番好き", re.I)
_CHOICE = re.compile(
    r"\b(?:chose|chooses?|picked|picks?|prefer(?:s|red)?|enjoy(?:s|ed)?|lik(?:es?|ed)|"
    r"lov(?:es?|ed)|favou?rites?)\b|골랐|꼽|좋아|최애|選ん|挙げ|好き|お気に入り", re.I,
)
_REPORTED = re.compile(
    r"\b(?:said|says?|explained|recalled|described|noted|told|answered|added|mentioned|"
    r"referenced|cited|wanted|decided|shared)\b|말했|설명|답했|밝혔|語っ|話し|述べ|答え", re.I,
)
_ANSWER_LINE = re.compile(r"^(?:\*\*)?([\w’' .-]{2,65})[:：](?:\*\*)?\s*\S")


def interview_page(title: str, url: str = "") -> bool:
    return bool(_INTERVIEW.search(title) or re.search(r"/interviews?/", urlsplit(url).path, re.I))


def interview_subject(title: str, body: str, url: str = "") -> str | None:
    if not interview_page(title, url):
        return None
    if _PREFERENCE.search(title):
        return "preference"
    if _INTERVIEW_LABEL.search(title.strip()) or re.search(r"\binterview\s+[–—-]\s+\S", title, re.I) or re.search(
        r"(?:[?？][*_]*\s*\n\s*(?:\*\*)?[\w’' .-]{2,65}[:：]|"
        r"\bwe (?:asked|spoke (?:with|to)|sat down with)\b)", body, re.I,
    ):
        return "interview"
    return None


def interview_search_passages(body: str, subject: str) -> str:
    """Spend part of the same retrieval query on actual answers, including reported choices."""
    selected = []
    for line in body.splitlines():
        line = line.strip()
        if (line and not line.startswith(("#", "|", "!", "[", "- "))
                and not re.search(r"\bwe asked\b", line, re.I)
                and not line.rstrip("*_").endswith(("?", "？"))
                and (_CHOICE.search(line) or (subject != "preference" and (
                    _ANSWER_LINE.match(line) or _REPORTED.search(line)
                )))):
            selected.append(line[:220])
            if len(selected) == 3:
                break
    return " ".join(selected)


ANSWER_SCHEMA = {
    "anyOf": [{"type": "null"}, {
        "type": "object", "additionalProperties": False,
        "properties": {key: {"type": "string"} for key in ("speaker", "subject", "detail", "reference_excerpt")},
        "required": ["speaker", "subject", "detail", "reference_excerpt"],
    }],
}

ANSWER_VERIFICATION = (
    "For an interview, preserve the actual participant's own answer to the article's question. "
    "For a favorite, take the named person's choice and the reason that same person gave for it. "
    "A reporter may describe it in adjacent sentences in one passage. Copy that contiguous passage, "
    "including the name when reported. Set interview_answer only for such a verified answer. "
    "Copy its speaker, a short exact subject span (the chosen character for a preference), and a "
    "separate exact detail span (their reason for that choice, or what they actually did or explained). "
    "Exclude staff credits, plot summaries, the question itself, guesses and other people's reasons. "
    "For status supported, copy a contiguous reference_excerpt from a cited RAG chunk that verifies "
    "who gave this specific answer and its detail. A character description or job listing cannot verify "
    "a personal answer. For a permitted attributed_quote use an empty reference_excerpt and apply "
    "the normal independent speaker-credit check. Set interview_answer to null on other passages."
)


def verified_interview_answer(
    raw: object, record: Mapping[str, object], subject: str, reference_texts: Sequence[str],
) -> dict[str, str] | None:
    if not isinstance(raw, Mapping):
        return None
    fields = ("speaker", "subject", "detail")
    if not all(isinstance(raw.get(key), str) and raw[key].strip() for key in fields):
        return None
    speaker, topic, detail = (raw[key].strip() for key in fields)
    passage = str(record["claim"])
    if (topic not in passage or detail not in passage or topic.casefold() == speaker.casefold()
            or detail.casefold() in (speaker.casefold(), topic.casefold()) or detail in topic
            or _CHOICE.fullmatch(detail)):
        return None
    if subject == "preference" and not _preference_detail(detail, speaker, topic):
        return None
    if record.get("kind") == "attributed_quote":
        if speaker != record["speaker"]:
            return None
    else:
        reference = raw.get("reference_excerpt")
        if (record.get("kind") != "rag_core" or speaker not in passage or not isinstance(reference, str)
                or not reference.strip() or not any(reference in text for text in reference_texts)):
            return None
        # A staff/character list cannot corroborate a reported choice, even if the verifier labels it core.
        if subject == "preference":
            if not (_CHOICE.search(passage) and _CHOICE.search(reference)):
                return None
            # For a Latin-script report, a different panelist's preference or a bare choice
            # cannot serve as the corroborating excerpt. Translated names are checked by the verifier.
            if _latin_script(reference + speaker + topic):
                words = _latin_words(reference)
                if (not words.intersection(_latin_words(speaker)) or not words.intersection(_latin_words(topic))
                        or not _preference_detail(reference, speaker, topic)):
                    return None
        label = _ANSWER_LINE.match(reference)
        printed_turn = label and label[1].strip().casefold() in speaker.casefold().split()
        if label and label[1].strip().casefold() == speaker.casefold():
            printed_turn = True
        if not (_REPORTED.search(reference) or _CHOICE.search(reference) or printed_turn):
            return None
    return {"speaker": speaker, "subject": topic, "detail": detail}


def _latin_words(text: str) -> set[str]:
    plain = "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                    if not unicodedata.combining(char))
    return set(re.findall(r"[a-z]+", plain))


def _latin_script(text: str) -> bool:
    return all(not char.isalpha() or char.isascii() or "LATIN" in unicodedata.name(char, "") for char in text)


def _preference_detail(text: str, speaker: str, topic: str) -> bool:
    if not _latin_script(text):
        return True
    remaining = _latin_words(_CHOICE.sub("", text)) - _latin_words(speaker + " " + topic)
    return bool(remaining.difference({
        "a", "an", "the", "i", "my", "he", "she", "his", "her", "their", "is", "was", "s", "as",
        "has", "had", "for", "of", "one", "it", "him", "character", "characters", "player", "team",
    }))
