"""Add provisional interest tags to already imported Agent articles.

Preview: python -m app.seeds.agent_interests --git-ref f645df0
Apply:   python -m app.seeds.agent_interests --git-ref f645df0 --apply

Only Subtopic and ContentTag rows are added. Existing article data, work tags,
subscriptions, and Agent provenance are retained. The JSON mapping records the
temporary categories and evidence; it is not a finalized service taxonomy.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentTag, Subtopic
from app.seeds.agent_contents import (
    TOPICS,
    Article,
    ImportConflict,
    digest,
    inspect_existing,
    load_committed_articles,
    required_string,
)


MAPPING_PATH = Path(__file__).with_name("agent_interest_topics.json")
CATEGORIES = {"genre", "mood", "content_type", "artist", "director"}


def validate_mapping(articles: list[Article], mapping: dict, provenance: dict) -> None:
    """Reject incomplete or ambiguous mappings before any database mutation."""
    if not isinstance(mapping, dict) or mapping.get("version") != 1:
        raise ValueError("Unsupported interest mapping")
    required_string(mapping.get("basis"), "interest mapping basis")
    if mapping.get("source_ref") != provenance.get("git_commit") or not mapping.get("source_ref"):
        raise ValueError("Interest mapping source_ref does not match the committed article source")
    catalogs = mapping.get("topics")
    if not isinstance(catalogs, dict) or set(catalogs) != set(TOPICS):
        raise ValueError("Interest mapping must contain movie, music, and anime catalogs")
    names_by_topic = {}
    for code, entries in catalogs.items():
        if not isinstance(entries, list) or len(entries) != 7:
            raise ValueError(f"Exactly seven interests are required: {code}")
        names = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"Invalid interest catalog entry: {code}")
            name = required_string(entry.get("name"), f"{code}.interest name", 100)
            if name != entry["name"] or name in names:
                raise ValueError(f"Duplicate or untrimmed interest name: {code}/{name}")
            if entry.get("category") not in CATEGORIES:
                raise ValueError(f"Unknown interest category: {code}/{name}")
            names.add(name)
        names_by_topic[code] = names
    items = mapping.get("items")
    article_ids = {article.raw["id"] for article in articles}
    if not articles or len(article_ids) != len(articles):
        raise ValueError("Articles must be nonempty and have unique ids")
    if not isinstance(items, dict) or set(items) != article_ids:
        raise ValueError("Interest mapping ids must exactly match the committed articles")
    used = {code: set() for code in TOPICS}
    for article in articles:
        item_id = article.raw["id"]
        entry = items[item_id]
        if not isinstance(entry, dict) or entry.get("topic_code") != article.topic_code:
            raise ValueError(f"Interest mapping topic mismatch: {item_id}")
        tags = entry.get("tags")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) for tag in tags):
            raise ValueError(f"Missing or invalid interest tags: {item_id}")
        if len(set(tags)) != len(tags) or not set(tags).issubset(names_by_topic[article.topic_code]):
            raise ValueError(f"Unknown or duplicate interest tag: {item_id}")
        required_string(entry.get("evidence"), f"{item_id}.interest evidence")
        used[article.topic_code].update(tags)
    for code, names in names_by_topic.items():
        if used[code] != names:
            raise ValueError(f"Every interest must be linked to at least one article: {code}")


async def import_interests(
    db: AsyncSession, articles: list[Article], mapping: dict, provenance: dict, *, apply: bool = False
) -> dict:
    """Caller owns the transaction; this function never commits or deletes."""
    validate_mapping(articles, mapping, provenance)
    if apply and db.bind.dialect.name == "postgresql":
        # Share the article importer's lock to serialize Subtopic creation.
        await db.execute(text("SELECT pg_advisory_xact_lock(684239107521)"))
    topics, subtopics, existing_contents = await inspect_existing(db, articles)
    missing = [article.raw["id"] for article in articles if article.import_key not in existing_contents]
    if missing:
        raise ImportConflict(f"Import all committed articles before adding interests: {', '.join(missing)}")

    needed_subtopics = []
    for code, entries in mapping["topics"].items():
        for entry in entries:
            name = entry["name"]
            matches = subtopics.get((topics[code].topic_id, name), [])
            if len(matches) > 1:
                raise ImportConflict(f"Ambiguous existing interest: {code}/{name}")
            if not matches:
                needed_subtopics.append((code, name))
    existing_tags = {
        (tag.content_id, tag.subtopic_id): tag.topic_id
        for tag in (await db.scalars(select(ContentTag).where(
            ContentTag.content_id.in_(list(existing_contents.values()))
        ))).all()
    }
    needed_tags = []
    already_linked = 0
    for article in articles:
        content_id = existing_contents[article.import_key]
        topic_id = topics[article.topic_code].topic_id
        for name in mapping["items"][article.raw["id"]]["tags"]:
            subjects = subtopics.get((topic_id, name), [])
            key = (content_id, subjects[0].subtopic_id) if subjects else None
            if key in existing_tags:
                if existing_tags[key] != topic_id:
                    raise ImportConflict(f"Existing content tag has a mismatched topic: {article.raw['id']}/{name}")
                already_linked += 1
            else:
                needed_tags.append((content_id, article.topic_code, name))

    report = {
        "mode": "apply" if apply else "dry_run",
        "source": provenance,
        "mapping_sha256": digest(mapping),
        "basis": mapping["basis"],
        "articles": len(articles),
        "by_topic": dict(Counter(article.topic_code for article in articles)),
        "new_subtopics": [{"topic": code, "name": name} for code, name in needed_subtopics],
        "new_content_tags": len(needed_tags),
        "already_linked": already_linked,
        "catalog": mapping["topics"],
    }
    if not apply:
        return report
    for code, name in needed_subtopics:
        subject = Subtopic(topic_id=topics[code].topic_id, subtopic_name=name,
                           parent_subtopic_id=None, depth_level=1)
        db.add(subject)
        await db.flush()
        subtopics[(subject.topic_id, name)] = [subject]
    for content_id, code, name in needed_tags:
        topic_id = topics[code].topic_id
        db.add(ContentTag(content_id=content_id, topic_id=topic_id,
                          subtopic_id=subtopics[(topic_id, name)][0].subtopic_id))
    await db.flush()
    return report


async def run(ref: str, apply: bool) -> dict:
    from app.db.session import AsyncSessionLocal, engine

    articles, provenance = load_committed_articles(ref)
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    engine.echo = False
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                if not apply and engine.dialect.name == "postgresql":
                    await db.execute(text("SET TRANSACTION READ ONLY"))
                report = await import_interests(db, articles, mapping, provenance, apply=apply)
        return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", default="origin/main", help="Commit/ref containing the imported Agent articles")
    parser.add_argument("--apply", action="store_true", help="Add tags in one transaction (default: read-only preview)")
    args = parser.parse_args()
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                result = runner.run(run(args.git_ref, args.apply))
        else:
            result = asyncio.run(run(args.git_ref, args.apply))
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Interest import stopped: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
