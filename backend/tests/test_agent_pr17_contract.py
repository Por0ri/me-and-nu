"""Agent PR17 persistence -> Checking carousel contract, entirely offline.

These synthetic fixtures exercise the real agent SQLite repository and the
service importer. They are not generated or approved production content.
"""

import hashlib
import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.models import AgentRun, Content, ContentSlide, Draft
from app.repositories import carousel_repository
from app.seeds import carousel_contents as seed
from app.services import feed_service
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_carousel_import import AsyncAdapter
from test_carousel_import import engine as engine
from test_carousel_import import raw_bundle as raw_bundle


@pytest.fixture
def agent_contract(tmp_path, monkeypatch, raw_bundle):
    source = Path(__file__).resolve().parents[2] / "agents" / "ani_me;nu" / "src"
    monkeypatch.syspath_prepend(str(source))
    config = importlib.import_module("ani_menu_agent.config")
    models = importlib.import_module("ani_menu_agent.models")
    storage = importlib.import_module("ani_menu_agent.storage")
    moment = datetime.now(UTC)
    settings = config.Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "agent.sqlite3"),
        OPENAI_API_KEY="",
        ANI_MENU_DATABASE_URL=None,
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(storage, "_utc_now", lambda: moment)
    repository = storage.AgentRepository(path=tmp_path / "agent.sqlite3")

    def persist(content_type="news"):
        payload = deepcopy(raw_bundle["items"][0]["package"])
        payload.update(
            topic="Synthetic contract check",
            content_kind="information",
            content_type=content_type,
            request_id=f"synthetic-pr17-{content_type}",
            feed_valid_until=(moment + timedelta(days=7)).isoformat(),
            purge_at=(moment + timedelta(days=37)).isoformat(),
            work_match_evidence=[
                {
                    "menu_work_id": 1,
                    "basis": "article_title",
                    "matched_value": "Synthetic contract evidence",
                    "confidence": 1.0,
                    "evidence_url": "https://example.com/official",
                }
            ],
        )
        payload["sources"][0]["publisher"] = "Synthetic fixture publisher"
        for card in payload["cards"]:
            card["image_id"] = "synthetic-art"
            card["layout"]["text_overlay_box"] = {
                "x": 0.06,
                "y": 0.68,
                "width": 0.88,
                "height": 0.28,
            }
        package = models.ContentPackage.model_validate(payload)
        pngs = {
            card.page: (tmp_path / "cards" / f"{card.page}.png").read_bytes()
            for card in package.cards
        }
        package = repository.persist_rendered_cards(package, pngs)
        repository.persist_generated_content(
            package,
            "ready_for_feed",
            {"total": 100, "decision": "pass_candidate", "synthetic_contract_fixture": True},
        )
        return package

    def export(package):
        # CLI exports these same stored PNG bytes. No renderer or network runs here.
        with repository.connect() as connection:
            row = connection.execute(
                "SELECT package_json, quality_report_json FROM content_drafts WHERE request_id=?",
                (package.request_id,),
            ).fetchone()
        stored = json.loads(row["package_json"])
        assets = []
        for card in stored["cards"]:
            binary, media_type = repository.get_image_bytes(card["rendered_image_key"])
            assert media_type == "image/png"
            path = tmp_path / "cards" / f"{card['page']}.png"
            path.write_bytes(binary)
            assets.append(
                {
                    "page": card["page"],
                    "path": f"cards/{card['page']}.png",
                    "sha256": hashlib.sha256(binary).hexdigest(),
                }
            )
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "producer": "ani_menu_agent",
                    "topicCode": "anime",
                    "items": [
                        {
                            "package": stored,
                            "assets": assets,
                            "reviewEvidence": json.loads(row["quality_report_json"]),
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return bundle_path, seed.load_bundle(bundle_path, now=moment)

    return repository, persist, export, moment


@pytest.mark.parametrize(
    "content_type",
    [
        "news",
        "event",
        "guide",
        "people",
        "goods",
        "review",
        "humor",
        "character",
        "curation",
        "music",
    ],
)
def test_current_agent_types_and_overlay_survive_service_bundle(agent_contract, content_type):
    repository, persist, export, _ = agent_contract
    package = persist(content_type)
    repository.enqueue_for_feed(package)
    _, prepared = export(package)
    item = prepared.items[0]
    assert item.package.content_type == content_type
    assert item.package.menu_work_ids == package.menu_work_ids == [1]
    assert item.raw.package["work_match_evidence"] == [
        evidence.model_dump(mode="json") for evidence in package.work_match_evidence
    ]
    assert item.package.detail_body == package.detail_body
    with repository.connect() as connection:
        stored_cards = connection.execute(
            "SELECT page, overlay_json FROM content_cards WHERE request_id=? ORDER BY page",
            (package.request_id,),
        ).fetchall()
    for record, card in zip(stored_cards, item.package.cards, strict=True):
        overlay = json.loads(record["overlay_json"])
        assert overlay["image_key"] == card.rendered_image_key
        assert overlay["title_lines"] == card.title_lines
        assert overlay["text_region"] == card.layout.text_overlay_box.model_dump()
        assert hashlib.sha256(item.images[card.page]).hexdigest() == card.rendered_image_key


@pytest.mark.asyncio
async def test_agent_ready_queue_does_not_bypass_service_review_and_overlay_is_public(
    agent_contract,
    engine,
    tmp_path,
):
    repository, persist, export, moment = agent_contract
    package = persist()
    repository.enqueue_for_feed(package)
    bundle_path, prepared = export(package)
    with Session(engine) as session, session.begin():
        db = AsyncAdapter(session)
        await seed.import_bundle(db, prepared, apply=True, now=moment)
        content = session.scalar(select(Content))
        assert (content.status, content.judgment_status) == ("pending_review", "needs_review")
        assert (
            session.scalar(select(Content).where(carousel_repository.public_content_condition()))
            is None
        )
        assert await carousel_repository.get_slide_image(db, content.content_id, 1) is None
        assert (
            session.scalar(select(AgentRun)).output_payload["package"]
            == prepared.items[0].raw.package
        )
        assert session.scalar(select(Draft)).structure_template["menu_work_ids"] == [1]
        review_path = tmp_path / "synthetic-review.json"
        review_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "records": [
                        {
                            "digest": prepared.items[0].digest,
                            "decision": "approved",
                            "reviewedBy": "synthetic-contract-test",
                            "reviewedAt": moment.isoformat(),
                            "comment": "Synthetic fixture only; not an actual content approval.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        reviewed = seed.load_bundle(bundle_path, review_record_file=review_path, now=moment)
        await seed.import_bundle(db, reviewed, apply=True, now=moment)
        assert (
            session.scalar(select(Content).where(carousel_repository.public_content_condition()))
            is content
        )
        slides = await feed_service._slides(db, content)
        assert len(slides) == 3
        for public, expected in zip(slides, package.cards, strict=True):
            wire = public.model_dump(by_alias=True)
            assert wire["titleLines"] == expected.title_lines
            assert wire["bodySentences"] == expected.body_sentences
            assert wire["layout"]["textOverlayBox"] == expected.layout.text_overlay_box.model_dump()
            assert (wire["width"], wire["height"]) == (1308, 1632)
            assert (
                await carousel_repository.get_slide_image(db, content.content_id, expected.page)
                == prepared.items[0].images[expected.page]
            )
        assert session.query(ContentSlide).count() == 3
        assert (await seed.import_bundle(db, reviewed, apply=True, now=moment))["unchanged"] == 1


def test_short_agent_package_stays_outside_checking_carousel_contract(agent_contract, tmp_path):
    _, persist, export, moment = agent_contract
    package = persist()
    bundle_path, _ = export(package)
    document = json.loads(bundle_path.read_text(encoding="utf-8"))
    document["items"][0]["package"]["cards"] = document["items"][0]["package"]["cards"][:2]
    document["items"][0]["assets"] = document["items"][0]["assets"][:2]
    bundle_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(seed.CarouselImportError):
        seed.load_bundle(bundle_path, now=moment)
