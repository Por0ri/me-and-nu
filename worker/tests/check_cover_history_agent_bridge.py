"""Worker -> Agent cover-history integration, entirely local and without keys.

Run with the Agent venv and PYTHONPATH=/app:/opt/ani-agent/src. Separate attempt
SQLite databases intentionally contain no shared history; the sealed JSON is
the only cross-attempt bridge. This file is outside Worker unittest discovery.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from menu_worker.cover_history import build_snapshot, history_environment
from menu_worker.job_artifacts import artifact_directory, sha256
from menu_worker import real_generation

from ani_menu_agent.config import get_settings
from ani_menu_agent.cover_history import CoverHistoryError, validate_cover_history_environment
from ani_menu_agent.image_selection import cover_usable_editorial_images, distinct_editorial_images, image_visual_keys
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentKind, ContentPackage, CopyCard, ImageAsset, RightsStatus, SourceEvidence
from ani_menu_agent.quality import check_images
from ani_menu_agent.storage import AgentRepository


class CoverHistoryAgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="cover-bridge-"))).resolve()
        self.enterContext(patch.dict(os.environ))
        for key in list(os.environ):
            if key.startswith(("ANI_MENU_COVER_HISTORY_", "ANIME_COVER_HISTORY_BOOTSTRAP_")):
                os.environ.pop(key)
        for key in ("OPENAI_API_KEY", "LLM_KEY", "ANI_MENU_DATABASE_URL", "DATABASE_URL"):
            os.environ.pop(key, None)
        os.environ["ANI_MENU_DATABASE_BACKEND"] = "sqlite"
        os.environ["ANI_MENU_DATABASE_PATH"] = str(self.root / "unused-default.sqlite3")
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        # sqlite3's transaction context does not close the handle. Track real
        # connections so temporary DB removal also works on Windows.
        connections = []
        connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        self.enterContext(patch("ani_menu_agent.storage.sqlite3.connect", side_effect=tracked_connect))
        self.addCleanup(lambda: [connection.close() for connection in connections])
        # A future accidental HTTP path fails this test immediately.
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Network is forbidden")))
        self.source = SourceEvidence(id="one_piece_official", title="Official goods",
            url="https://one-piece.com/news/83418/index.html", publisher="ONE PIECE", is_official=True)
        self.old = self.asset("old-cover", "a" * 64)
        self.other = self.asset("different-cover", "c" * 64)
        self.body = self.asset("old-detail", "d" * 64)
        self.prior_job = self.job()
        self.current_job = self.job()
        self.previous = self.package("crawl-bridge-old", self.old, self.body)
        self.first_db = AgentRepository(self.root / "attempt-first.sqlite3")
        self.second_db = AgentRepository(self.root / "attempt-second.sqlite3")
        with self.first_db.connect() as db:
            db.execute("INSERT INTO feed_queue(request_id,topic,package_json,queued_at) VALUES(?,?,?,?)",
                       (self.previous.request_id, self.previous.topic, self.previous.model_dump_json(), "2026-11-16T00:00:00Z"))
        self.seal_previous()

    def asset(self, name, fingerprint):
        return ImageAsset(id=name, url=f"https://one-piece.com/images/{name}.jpg",
            source_url=self.source.url, storage_key=f"bridge-{name}", fingerprint=fingerprint,
            official=True, rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
            rights_evidence_url=self.source.url, rights_checked_at="2026-11-16T00:00:00Z",
            attribution_text="ONE PIECE official image", width=1200, height=1200,
            download_status="downloaded", visual_match="direct")

    def job(self):
        return {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
            "job_kind": "anime_generate", "input_sha256": "e" * 64, "source_id": "one_piece_official",
            "canonical_url": str(self.source.url), "content_type": "goods", "menu_work_id": 8}

    def package(self, request_id, cover, detail):
        package = ContentPackage(request_id=request_id, topic="Official ONE PIECE goods",
            content_kind=ContentKind.INFORMATION, content_type="goods", menu_work_ids=[8],
            sources=[self.source], images=[cover, detail], detail_body="Offline source-backed fixture.")
        package.cards = build_cards([
            CopyCard(page=1, role="cover", title_lines=["공식 굿즈 소식"], image_id=cover.id),
            CopyCard(page=2, role="detail", title_lines=["상품 상세 정보"], image_id=detail.id),
        ], package.images)
        return package

    def seal_previous(self):
        from PIL import Image
        folder = artifact_directory(self.root, self.prior_job, create=True)
        card_dir = folder / "cards" / self.previous.request_id
        card_dir.mkdir(parents=True)
        for card in self.previous.cards:
            stream = BytesIO()
            Image.new("L", (1308, 1632), color=card.page).save(stream, "PNG")
            raw = stream.getvalue()
            (card_dir / f"card_{card.page:02d}.png").write_bytes(raw)
            card.rendered_image_key = sha256(raw)
        result = {"status": "ready_for_feed", "package": self.previous.model_dump(mode="json"),
            "expected_work_request": real_generation._request(self.prior_job),
            "review_mode": "llm", "editorial_decision": "approved_for_feed",
            "quality_report": {"decision": "pass_candidate", "hard_blockers": []},
            "qa_report": [], "language_review": {"tone": "pass"},
            "usage_records": [{"stage": "review", "total_tokens": 0}]}
        # A local contract fixture, never a provider-generated or service-imported result.
        (folder / "result.json").write_bytes(real_generation._dump(result))
        (folder / "launch.json").write_bytes(real_generation._dump(real_generation._identity(self.prior_job)))
        (folder / "exit.json").write_text('{"returncode":0}', encoding="utf-8")
        self.prior_job.update(real_generation.validate_real_artifacts(self.root, self.prior_job))

    def bridge(self):
        data = build_snapshot(self.root, self.current_job, [self.prior_job])
        path = self.root / "current-cover-history.json"
        path.write_bytes(data)
        os.environ.update(history_environment(path, data, self.current_job))
        get_settings.cache_clear()
        return path, data

    def test_sealed_prior_cover_crosses_attempt_db_boundary_and_blocks_alias(self):
        with self.second_db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feed_queue").fetchone()[0], 0)
        self.assertEqual(self.second_db.prior_cover_image_fingerprints(exclude_request_id="crawl-current"), [])
        _path, raw = self.bridge()
        entry, = json.loads(raw)["entries"]
        self.assertEqual(entry["origin"]["result_sha256"], self.prior_job["result_sha256"])
        self.assertEqual(entry["image"], self.old.model_dump(mode="json"))
        validated, = validate_cover_history_environment()
        self.assertEqual(validated.visual_keys, frozenset(image_visual_keys(self.old)))
        alias = self.asset("cdn-alias", self.old.fingerprint)
        package = self.package("crawl-current", alias, self.other)
        package.prior_image_fingerprints = self.second_db.prior_cover_image_fingerprints(exclude_request_id=package.request_id)
        self.assertEqual([item.id for item in cover_usable_editorial_images(package.images, package.sources, package)], [self.other.id])
        self.assertIn("IMAGE_USED_BEFORE", {issue.code for issue in check_images(package)})
        with self.second_db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feed_queue").fetchone()[0], 0)

    def test_different_cover_and_historical_image_on_body_are_allowed(self):
        self.bridge()
        package = self.package("crawl-new-cover", self.other, self.old)
        package.prior_image_fingerprints = self.second_db.prior_cover_image_fingerprints(exclude_request_id=package.request_id)
        self.assertEqual([item.id for item in cover_usable_editorial_images(package.images, package.sources, package)], [self.other.id])
        self.assertEqual({item.id for item in distinct_editorial_images(package.images, package.sources, package,
                                                                       card_role="detail")}, {self.other.id, self.old.id})
        self.assertNotIn("IMAGE_USED_BEFORE", {issue.code for issue in check_images(package)})
        self.assertNotIn("IMAGE_REUSED_RAW", {issue.code for issue in check_images(package)})
        self.assertTrue(set(package.prior_image_fingerprints).isdisjoint(image_visual_keys(self.body)))

    def test_own_request_is_excluded_without_sharing_agent_database(self):
        self.bridge()
        self.assertEqual(self.second_db.prior_cover_image_fingerprints(exclude_request_id=self.previous.request_id), [])
        with self.first_db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feed_queue").fetchone()[0], 1)
        with self.second_db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feed_queue").fetchone()[0], 0)

    def test_tamper_after_admission_is_rejected_by_repository(self):
        path, raw = self.bridge()
        self.assertTrue(validate_cover_history_environment())
        path.write_bytes(raw + b" ")
        with self.assertRaisesRegex(CoverHistoryError, "hash_mismatch"):
            self.second_db.prior_cover_image_fingerprints(exclude_request_id="crawl-current")


if __name__ == "__main__":
    unittest.main(verbosity=2)
