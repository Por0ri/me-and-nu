"""Validate an anime article request against mounted producer data without network I/O."""
import csv
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


CATALOG_ROOT = Path("/app/anime-catalog")
_TITLE_COLUMNS = ("title_ko", "title_native", "title_romaji", "title_english")
_ARTICLE_PARAMETERS = {
    "id", "idx", "no", "p", "article_id", "articleid", "news_id", "newsid", "post_id", "postid",
    "slug", "story", "episode_id", "episodeid",
}
_PRIVATE_QUERY_WORDS = {
    "auth", "authorization", "bearer", "cookie", "credential", "credentials", "jwt", "key", "oauth",
    "passwd", "password", "pwd", "secret", "session", "sid", "sig", "signature", "token",
}
_NONPUBLIC_SUFFIXES = {"example", "invalid", "test", "local", "localhost", "internal", "onion", "arpa"}


class AnimeInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _private_query_key(key: str) -> bool:
    words = re.split(r"[\W_]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower())
    compact = "".join(words)
    return (not _PRIVATE_QUERY_WORDS.isdisjoint(words)
            or compact.startswith(("xamz", "xgoog"))
            or compact.endswith(("token", "secret", "password", "signature", "credential", "sessionid",
                                 "apikey", "accesskey", "accesskeyid", "privatekey")))


def _anime_urls(value: str) -> tuple[str, str]:
    """Return the article identity and the original public request spelling, without network I/O."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048 or "\\" in value
            or any(not char.isprintable() or char.isspace() for char in value)
            or re.search(r"%(?![a-fA-F0-9]{2})", value.partition("#")[0])):
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

    kept = []
    raw_fields = []
    seen = set()
    for raw_field, (key, item) in zip(parsed.query.split("&"), pairs):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in {"fbclid", "gclid"}:
            continue
        if (not re.fullmatch(r"[\w.~-]{1,80}", key) or _private_query_key(key) or lowered in seen
                or not re.fullmatch(r"[\w.,+~ -]{0,160}", item)):
            raise AnimeInputError("invalid_source_query")
        seen.add(lowered)
        kept.append((key, item))
        raw_fields.append(raw_field)
    path = "/".join(clean_segments).rstrip("/") or "/"
    canonical = urlunsplit(("https", host, path, urlencode(sorted(kept)), ""))
    stored = value.partition("#")[0]
    if len(raw_fields) != len(pairs):
        stored = stored.partition("?")[0]
        if raw_fields:
            stored += "?" + "&".join(raw_fields)
    if not parsed.path and "?" not in stored:
        stored += "/"
    if max(len(canonical), len(stored)) > 2048:
        raise AnimeInputError("invalid_source_url")
    return canonical, stored


def canonical_anime_url(value: str) -> str:
    return _anime_urls(value)[0]


def one_piece_news_path(source_id: str, canonical_url: str) -> str | None:
    path = canonical_url.partition("?")[0]
    if source_id == "one_piece_official" and re.fullmatch(r"https://one-piece\.com/news/[0-9]+/index\.html", path):
        return path
    return None


def generation_article_key(source_id: str, canonical_url: str) -> str:
    """One Piece's numbered news articles use ref for attribution. Keep all other query fields."""
    path = one_piece_news_path(source_id, canonical_url)
    if path is None:
        return canonical_url
    pairs = parse_qsl(urlsplit(canonical_url).query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if key.lower() != "ref"]
    return path + ("?" + urlencode(kept) if kept else "")


def source_url_for_storage(value: str) -> str:
    return _anime_urls(value)[1]


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


def _article_directory(path: str, listing: str) -> str:
    if not path.endswith("/") and PurePosixPath(listing).suffix:
        return listing.rsplit("/", 1)[0]
    return listing


def _is_registered_article(canonical: str, source: dict, sources: list[dict]) -> bool:
    """Reject foreign paths and assets; an in-scope listing alone is not an article."""
    article = urlsplit(canonical)
    registered = urlsplit(canonical_anime_url(source["url"]))
    if article.hostname != registered.hostname:
        raise AnimeInputError("source_host_mismatch")
    listing = registered.path.rstrip("/")
    # A registry endpoint can be a listing file (e.g. /news/index.html).
    directory = _article_directory(urlsplit(source["url"]).path, listing)
    path = article.path.rstrip("/")
    is_listing = path in {directory, listing, directory + "/index.html", directory + "/index.htm"}
    if not is_listing and not path.startswith(directory + "/"):
        raise AnimeInputError("source_path_mismatch")
    # An umbrella publisher endpoint must not authorize another registered series' pages.
    for other in sources:
        if other["id"] == source["id"] or other["collection"] not in {"html", "api"}:
            continue
        peer = urlsplit(other["url"])
        if peer.hostname != article.hostname:
            continue
        peer_directory = _article_directory(peer.path, peer.path.rstrip("/"))
        if len(peer_directory) >= len(directory) and (path == peer_directory or path.startswith(peer_directory + "/")):
            raise AnimeInputError("source_path_mismatch")
    suffix = PurePosixPath(path).suffix.lower()
    allowed_suffixes = {"", ".html", ".htm", ".php", ".aspx", ".jsp"}
    if source["collection"] == "api":
        allowed_suffixes.add(".json")
    if suffix not in allowed_suffixes:
        raise AnimeInputError("source_path_mismatch")
    return not is_listing or any(key.lower() in _ARTICLE_PARAMETERS and item.strip()
                                 for key, item in parse_qsl(article.query))


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


def validate_anime_source(source_id: str, source_url: str, content_type: str, menu_work_id: int,
                          canonical_url: str | None = None) -> str:
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
        is_article = _is_registered_article(canonical, source, sources)
        if canonical_url is not None:
            try:
                candidate = canonical_anime_url(canonical_url)
                if _is_registered_article(candidate, source, sources):
                    canonical, is_article = candidate, True
            except AnimeInputError:
                pass  # A publisher hint is optional; the request must still identify an article.
        if not is_article:
            raise AnimeInputError("source_path_mismatch")
        _validate_source_work(source, menu_work_id)
    except AnimeInputError:
        raise
    except OSError:
        raise AnimeInputError("catalog_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, csv.Error):
        raise AnimeInputError("invalid_catalog") from None
    return canonical
