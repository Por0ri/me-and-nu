"""Import committed Agent articles without regenerating them or changing the schema.

Preview: python -m app.seeds.agent_contents --git-ref f645df0
Apply:   python -m app.seeds.agent_contents --git-ref f645df0 --apply
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, AgentRunSource, Content, ContentSource, ContentTag, Draft, Subtopic, Topic


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = "agent/contents/contents.json"
MAPPING_PATH = Path(__file__).with_name("agent_content_subtopics.json")
IMPORT_PREFIX = "content-import:v1:"
TOPICS = {"movie": "영화", "music": "음악", "anime": "애니메이션"}
CONTENT_TYPES = {
    ("movie", "영화 정보"): "movie_info",
    ("movie", "영화 리뷰"): "movie_review",
    ("music", "음악 리뷰"): "music_review",
    ("anime", "사람"): "anime_people",
    ("anime", "신작소식"): "anime_news",
    ("anime", "작품리뷰"): "anime_review",
    ("anime", "제작이야기"): "anime_production",
}


class ImportConflict(ValueError):
    """The import would conflict with existing data; nothing should be overwritten."""


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def required_string(value: object, label: str, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or (limit and len(value.strip()) > limit):
        raise ValueError(f"Invalid {label}")
    return value.strip()


@dataclass(frozen=True)
class Article:
    raw: dict
    topic_code: str
    content_type: str
    subtopics: tuple[str, ...]
    mapping_evidence: str
    sources: tuple[tuple[str, str], ...]
    published_at: datetime
    fingerprint: str

    @property
    def import_key(self) -> str:
        return IMPORT_PREFIX + self.raw["id"]


def prepare_articles(records: object, mapping: dict) -> list[Article]:
    if not isinstance(records, list) or not records:
        raise ValueError("The manifest must contain articles")
    if not isinstance(mapping, dict) or mapping.get("version") != 1 or not isinstance(mapping.get("items"), dict):
        raise ValueError("Unsupported subtopic mapping")
    articles = []
    seen = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("Each article must be an object")
        item_id = required_string(raw.get("id"), "id", 70)
        if not re.fullmatch(r"(movie|music|anime)-[0-9a-f]{10}", item_id) or item_id in seen:
            raise ValueError(f"Invalid or duplicate article id: {item_id}")
        seen.add(item_id)
        code = item_id.split("-", 1)[0]
        if raw.get("topicId") != f"topic-{code}":
            raise ValueError(f"Topic mismatch: {item_id}")
        required_string(raw.get("title"), f"{item_id}.title", 300)
        required_string(raw.get("body"), f"{item_id}.body")
        required_string(raw.get("summary"), f"{item_id}.summary")
        required_string(raw.get("origin"), f"{item_id}.origin")
        required_string(raw.get("agent"), f"{item_id}.agent", 50)
        version = raw.get("agentVersion")
        if not isinstance(version, str) or len(version) > 40:
            raise ValueError(f"Invalid agentVersion: {item_id}")
        original_subtopic = required_string(raw.get("subtopic"), "original subtopic")
        content_type = CONTENT_TYPES.get((code, original_subtopic))
        if content_type is None:
            raise ValueError(f"Unknown article type: {item_id}")
        tags = mapping["items"].get(item_id)
        if not isinstance(tags, dict) or tags.get("topic_code") != code:
            raise ValueError(f"Missing or mismatched subtopic mapping: {item_id}")
        names = tags.get("subtopics")
        if not isinstance(names, list) or not names:
            raise ValueError(f"Missing subject names: {item_id}")
        names = tuple(dict.fromkeys(required_string(name, "subtopic name", 100) for name in names))
        evidence = required_string(tags.get("evidence"), "mapping evidence")
        sources = []
        seen_urls = set()
        if not isinstance(raw.get("sources"), list):
            raise ValueError(f"Missing sources: {item_id}")
        for source in raw["sources"]:
            if not isinstance(source, dict):
                raise ValueError(f"Invalid source entry: {item_id}")
            url = required_string(source.get("url"), "source URL", 1000)
            parsed = urlsplit(url)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or any(ch.isspace() for ch in url)):
                raise ValueError(f"Invalid source URL: {item_id}")
            _ = parsed.port
            name = required_string(source.get("name"), "source name", 300)
            if url not in seen_urls:
                sources.append((url, name))
                seen_urls.add(url)
        if not sources:
            raise ValueError(f"No usable source: {item_id}")
        stamp = datetime.fromisoformat(required_string(raw.get("createdAt"), "createdAt"))
        # The committed export uses Korean local timestamps without an offset.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone(timedelta(hours=9)))
        fingerprint = digest({"record": raw, "subtopic_mapping": tags})
        articles.append(Article(raw, code, content_type, names, evidence, tuple(sources), stamp.astimezone(timezone.utc), fingerprint))
    return articles


def load_committed_articles(ref: str) -> tuple[list[Article], dict]:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT)

    commit = git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    manifest = git("show", f"{commit}:{MANIFEST_PATH}")
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    articles = prepare_articles(json.loads(manifest), mapping)
    return articles, {"git_commit": commit, "path": MANIFEST_PATH, "manifest_sha256": hashlib.sha256(manifest).hexdigest()}


async def inspect_existing(db: AsyncSession, articles: list[Article]) -> tuple[dict, dict, dict]:
    topics = {row.topic_code: row for row in (await db.scalars(select(Topic))).all()}
    subtopics = {}
    for row in (await db.scalars(select(Subtopic))).all():
        subtopics.setdefault((row.topic_id, row.subtopic_name), []).append(row)
    existing = {}
    for article in articles:
        topic = topics.get(article.topic_code)
        if topic is not None and not topic.is_active:
            raise ImportConflict(f"Topic is inactive: {article.topic_code}")
        for name in article.subtopics:
            if topic is not None and len(subtopics.get((topic.topic_id, name), [])) > 1:
                raise ImportConflict(f"Ambiguous existing subtopic: {article.topic_code}/{name}")
        run = await db.scalar(select(AgentRun).where(AgentRun.queue_task_id == article.import_key))
        if run is not None:
            provenance = (run.input_payload or {}).get("import", {})
            if provenance.get("item_sha256") != article.fingerprint:
                raise ImportConflict(f"Imported article changed; review required: {article.raw['id']}")
            draft = await db.scalar(select(Draft).where(Draft.agent_run_id == run.agent_run_id))
            content = None if draft is None else await db.scalar(select(Content).where(Content.source_draft_id == draft.draft_id))
            if content is None or topic is None or content.topic_id != topic.topic_id:
                raise ImportConflict(f"Incomplete or mismatched previous import: {article.raw['id']}")
            existing[article.import_key] = content.content_id
        elif topic is not None:
            matches = (await db.scalars(select(Content).where(
                Content.topic_id == topic.topic_id,
                Content.title == article.raw["title"].strip(),
                Content.body == article.raw["body"].strip(),
            ))).all()
            if matches:
                # Do not adopt or modify a pre-existing article owned by another path.
                raise ImportConflict(f"Matching content already exists outside this importer: {article.raw['id']} / {matches[0].content_id}")
    return topics, subtopics, existing


async def import_articles(db: AsyncSession, articles: list[Article], provenance: dict, *, apply: bool = False) -> dict:
    """Caller owns a transaction. This function never commits independently."""
    if apply and db.bind.dialect.name == "postgresql":
        # One batch lock also serializes creation of shared subject names.
        await db.execute(text("SELECT pg_advisory_xact_lock(684239107521)"))
    topics, subtopics, existing = await inspect_existing(db, articles)
    new_articles = [a for a in articles if a.import_key not in existing]
    needed_topics = sorted({a.topic_code for a in new_articles if a.topic_code not in topics})
    needed_subtopics = sorted({(a.topic_code, name) for a in new_articles for name in a.subtopics
                              if a.topic_code not in topics or (topics[a.topic_code].topic_id, name) not in subtopics})
    report = {
        "mode": "apply" if apply else "dry_run", "source": provenance,
        "articles": len(articles), "by_topic": dict(Counter(a.topic_code for a in articles)),
        "already_imported": len(existing), "new_contents": len(new_articles),
        "new_topics": needed_topics, "new_subtopics": [{"topic": code, "name": name} for code, name in needed_subtopics],
        "new_sources": sum(len(a.sources) for a in new_articles),
        "duplicate_source_rows_removed": sum(len(a.raw["sources"]) - len(a.sources) for a in articles),
        "content_ids": dict(existing),
    }
    if not apply:
        return report
    for code in needed_topics:
        topic = Topic(topic_code=code, topic_name=TOPICS[code], description=f"{TOPICS[code]} 콘텐츠", sort_order={"movie": 1, "music": 2, "anime": 3}[code], is_active=True)
        db.add(topic)
        await db.flush()
        topics[code] = topic
    for code, name in needed_subtopics:
        subject = Subtopic(topic_id=topics[code].topic_id, subtopic_name=name, parent_subtopic_id=None, depth_level=1)
        db.add(subject)
        await db.flush()
        subtopics[(subject.topic_id, name)] = [subject]
    for article in new_articles:
        raw = article.raw
        topic_id = topics[article.topic_code].topic_id
        now = datetime.now(timezone.utc)
        basis = {**provenance, "item_id": raw["id"], "item_sha256": article.fingerprint,
                 "origin": raw["origin"], "method": "committed_article_import", "original_subtopic": raw["subtopic"],
                 "mapped_subtopics": list(article.subtopics), "mapping_evidence": article.mapping_evidence,
                 "requires_review": True, "judgment_logs_available": False}
        run = AgentRun(topic_id=topic_id, agent_code=raw["agent"], agent_version=raw["agentVersion"] or "unknown",
                       queue_task_id=article.import_key, status="succeeded", outcome="publish_candidate", node_count=0,
                       started_at=now, finished_at=now, input_payload={"import": basis, "record": raw},
                       output_payload={"method": "committed_article_import", "regenerated": False, "judgment_logs_available": False})
        db.add(run)
        await db.flush()
        draft = Draft(agent_run_id=run.agent_run_id, topic_id=topic_id, production_type="ai", content_type=article.content_type,
                      title=raw["title"].strip(), body=raw["body"].strip(), status="published",
                      factcheck_result={"requires_review": True, "reason": "Original judgment logs are not included in the committed export."})
        db.add(draft)
        await db.flush()
        content = Content(source_draft_id=draft.draft_id, topic_id=topic_id, production_type="ai", content_type=article.content_type,
                          title=draft.title, body=draft.body, summary=raw["summary"].strip(), published_at=article.published_at,
                          status="active", judgment_status="needs_review", ai_judgment_basis=basis, is_sponsored=False)
        db.add(content)
        await db.flush()
        for order, (url, name) in enumerate(article.sources, start=1):
            source = AgentRunSource(agent_run_id=run.agent_run_id, source_type="article", source_url=url, source_title=name)
            db.add(source)
            await db.flush()
            db.add(ContentSource(content_id=content.content_id, agent_run_source_id=source.agent_run_source_id,
                                 source_url=url, source_title=name, source_role="primary" if order == 1 else "citation", citation_order=order))
        selected_tags = {subtopics[(topic_id, name)][0].subtopic_id for name in article.subtopics}
        # Preserve compatibility with already registered movie information/review filters.
        legacy = subtopics.get((topic_id, raw["subtopic"]), [])
        if len(legacy) == 1:
            selected_tags.add(legacy[0].subtopic_id)
        for subject_id in selected_tags:
            db.add(ContentTag(content_id=content.content_id, topic_id=topic_id, subtopic_id=subject_id))
        report["content_ids"][article.import_key] = content.content_id
    await db.flush()
    return report


async def run(ref: str, apply: bool) -> dict:
    from app.db.session import AsyncSessionLocal, engine

    articles, provenance = load_committed_articles(ref)
    engine.echo = False
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                if not apply and engine.dialect.name == "postgresql":
                    await db.execute(text("SET TRANSACTION READ ONLY"))
                report = await import_articles(db, articles, provenance, apply=apply)
        return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", default="origin/main", help="Commit/ref containing agent/contents/contents.json")
    parser.add_argument("--apply", action="store_true", help="Commit the entire import in one transaction (default: read-only preview)")
    args = parser.parse_args()
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                result = runner.run(run(args.git_ref, args.apply))
        else:
            result = asyncio.run(run(args.git_ref, args.apply))
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Import stopped: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
