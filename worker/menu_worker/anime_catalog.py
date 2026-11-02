"""Validate an anime article request against mounted producer data without network I/O."""
import csv
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


CATALOG_ROOT = Path("/app/anime-catalog")
_TITLE_COLUMNS = ("title_ko", "title_native", "title_romaji", "title_english")
_ARTICLE_PARAMETERS = {"id", "idx", "no", "p", "article_id", "articleid", "news_id", "newsid", "post_id", "postid"}
_LOCALE_PARAMETERS = {"lang", "locale"}
_TRACKING_PARAMETERS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id", "fbclid", "gclid"}
_NONPUBLIC_SUFFIXES = {"example", "invalid", "test", "local", "localhost", "internal", "onion", "arpa"}


class AnimeInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_anime_url(value: str) -> str:
    """Normalize the public URL syntax used for job identity. This does not resolve DNS."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048 or "#" in value or "\\" in value
            or any(not char.isprintable() or char.isspace() for char in value)
            or re.search(r"%(?![a-fA-F0-9]{2})", value)):
        raise AnimeInputError("invalid_source_url")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise AnimeInputError("invalid_source_url") from None
    labels = host.split(".")
    if (parsed.scheme != "https" or parsed.netloc.lower() not in {host, host + ":443"}
            or port not in {None, 443} or len(host) > 253 or len(labels) < 2
            or not re.fullmatch(r"[a-z]{2,63}", labels[-1]) or labels[-1] in _NONPUBLIC_SUFFIXES
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        raise AnimeInputError("invalid_source_url")

    path = parsed.path or "/"
    if "//" in path:
        raise AnimeInputError("invalid_source_path")
    clean_segments = []
    try:
        for segment in path.split("/"):
            decoded = unquote(segment, errors="strict")
            normalized = unicodedata.normalize("NFKC", decoded)
            if (normalized in {".", ".."} or any(char in normalized for char in "/\\?#%;:")
                    or any(not char.isprintable() or char.isspace() for char in normalized)):
                raise AnimeInputError("invalid_source_path")
            clean_segments.append(quote(decoded, safe="-._~"))
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32)
    except AnimeInputError:
        raise
    except (UnicodeError, ValueError):
        raise AnimeInputError("invalid_source_url") from None

    # Only public article identifiers and locale selectors belong in a submitted URL.
    # Tracking parameters do not identify the article. Other parameters can carry credentials.
    kept = []
    seen = set()
    for key, item in pairs:
        lowered = key.lower()
        if lowered in _TRACKING_PARAMETERS:
            continue
        if (lowered not in _ARTICLE_PARAMETERS | _LOCALE_PARAMETERS or lowered in seen
                or not re.fullmatch(r"[\w.~-]{1,160}", item)):
            raise AnimeInputError("invalid_source_query")
        seen.add(lowered)
        kept.append((key, item))
    result = urlunsplit(("https", host, "/".join(clean_segments), urlencode(sorted(kept)), ""))
    if len(result) > 2048:
        raise AnimeInputError("invalid_source_url")
    return result


def _load_source(source_id: str, content_type: str) -> tuple[dict, list[dict]]:
    with (CATALOG_ROOT / "sources.json").open(encoding="utf-8-sig") as stream:
        sources = json.load(stream)["sources"]
    with (CATALOG_ROOT / "source_type_map.json").open(encoding="utf-8-sig") as stream:
        type_map = json.load(stream)
    by_id = {row["id"]: row for row in sources}
    if len(by_id) != len(sources) or set(type_map["sources"]) - by_id.keys():
        raise AnimeInputError("invalid_catalog")
    if source_id not in by_id:
        raise AnimeInputError("unknown_source")
    source = by_id[source_id]
    if source["status"] != "active":
        raise AnimeInputError("source_inactive")
    if source["role"] not in {"primary", "editorial", "reference"} or source["collection"] not in {"html", "api"}:
        raise AnimeInputError("unsupported_source")
    allowed = type_map["sources"].get(source_id, [])
    if not isinstance(allowed, list) or not isinstance(type_map["types"], dict):
        raise AnimeInputError("invalid_catalog")
    if content_type not in type_map["types"] or content_type not in allowed:
        raise AnimeInputError("unsupported_content_type")
    return source, sources


def _article_directory(path: str) -> str:
    listing = path.rstrip("/")
    if not path.endswith("/") and PurePosixPath(listing).suffix:
        return listing.rsplit("/", 1)[0]
    return listing


def _validate_article_scope(canonical: str, source: dict, sources: list[dict]) -> None:
    article = urlsplit(canonical)
    registered = urlsplit(canonical_anime_url(source["url"]))
    if article.hostname != registered.hostname:
        raise AnimeInputError("source_host_mismatch")
    listing = registered.path.rstrip("/")
    # A registry endpoint can be a listing file (e.g. /news/index.html).
    directory = _article_directory(registered.path)
    path = article.path.rstrip("/")
    identifies_article = any(key.lower() in _ARTICLE_PARAMETERS for key, _ in parse_qsl(article.query))
    if path in {directory, listing, directory + "/index.html", directory + "/index.htm"}:
        if not identifies_article:
            raise AnimeInputError("source_path_mismatch")
    elif not path.startswith(directory + "/"):
        raise AnimeInputError("source_path_mismatch")
    # An umbrella publisher endpoint must not authorize another registered series' pages.
    for other in sources:
        if other["id"] == source["id"] or other["collection"] not in {"html", "api"}:
            continue
        peer = urlsplit(other["url"])
        if peer.hostname != article.hostname:
            continue
        peer_directory = _article_directory(peer.path)
        if len(peer_directory) >= len(directory) and (path == peer_directory or path.startswith(peer_directory + "/")):
            raise AnimeInputError("source_path_mismatch")
    suffix = PurePosixPath(path).suffix.lower()
    allowed_suffixes = {"", ".html", ".htm", ".php", ".aspx", ".jsp"}
    if source["collection"] == "api":
        allowed_suffixes.add(".json")
    if suffix not in allowed_suffixes:
        raise AnimeInputError("source_path_mismatch")


def _normalized_title(text: str) -> str:
    # Match typography using the producer's work_catalog._normalize_catalog_title rules.
    title = unicodedata.normalize("NFKC", text).casefold().replace("☆", "")
    title = re.sub(r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u9fff])", "", title)
    return " ".join(title.split())


def _validate_source_work(source: dict, menu_work_id: int) -> None:
    titles = {_normalized_title(source.get(key) or "") for key in ("work_title_ko", "work_title_original")} - {""}
    if not titles:
        raise AnimeInputError("source_work_unresolved")
    matches, existing = set(), set()
    with (CATALOG_ROOT / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"menu_work_id", *_TITLE_COLUMNS}.issubset(reader.fieldnames or ()):
            raise AnimeInputError("invalid_catalog")
        for row in reader:
            work_id = int(row["menu_work_id"])
            if work_id < 1 or work_id in existing:
                raise AnimeInputError("invalid_catalog")
            existing.add(work_id)
            if any(_normalized_title(row[field]) in titles for field in _TITLE_COLUMNS):
                matches.add(work_id)
    if menu_work_id not in existing:
        raise AnimeInputError("unknown_work")
    if len(matches) != 1:
        raise AnimeInputError("source_work_unresolved")
    if menu_work_id not in matches:
        raise AnimeInputError("source_work_mismatch")


def validate_anime_source(source_id: str, source_url: str, content_type: str, menu_work_id: int) -> str:
    """Validate current source, article scope, type and exact registered work. Return canonical URL."""
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", source_id):
        raise AnimeInputError("invalid_source_id")
    if type(menu_work_id) is not int or not 1 <= menu_work_id <= 2**63 - 1:
        raise AnimeInputError("invalid_menu_work_id")
    if not isinstance(content_type, str) or not re.fullmatch(r"[a-z][a-z_]{0,31}", content_type):
        raise AnimeInputError("unsupported_content_type")
    canonical = canonical_anime_url(source_url)
    try:
        source, sources = _load_source(source_id, content_type)
        _validate_article_scope(canonical, source, sources)
        _validate_source_work(source, menu_work_id)
    except AnimeInputError:
        raise
    except OSError:
        raise AnimeInputError("catalog_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, csv.Error):
        raise AnimeInputError("invalid_catalog") from None
    return canonical
