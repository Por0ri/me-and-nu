"""Incomplete, modified and foreign output must never become completed jobs."""
import binascii
import json
import struct
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from menu_worker.job_artifacts import ArtifactError, PIXEL, artifact_directory, generate_fake, sha256, validate_artifacts


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.job = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
                    "source_url": "https://example.invalid/success/test", "input_sha256": "a" * 64}

    def test_fixture_result_is_explicitly_simulated_and_separate_from_service_bundle(self):
        generate_fake(self.root, self.job)
        evidence = validate_artifacts(self.root, self.job)
        path = artifact_directory(self.root, self.job)
        result = json.loads((path / "result.json").read_text())
        self.assertEqual(evidence["outcome"], "ready_for_staging")
        self.assertEqual(result["producer"], "fake_ani_menu_agent")
        self.assertTrue(result["simulated"])
        self.assertEqual(result["llm_calls"], 0)
        self.assertFalse(result["service_database_changed"])
        self.assertNotIn("package", result)
        with self.assertRaises(ArtifactError):
            generate_fake(self.root, self.job)

    def test_incomplete_files_without_final_manifest_are_rejected(self):
        generate_fake(self.root, self.job)
        path = artifact_directory(self.root, self.job)
        (path / "manifest.json").unlink()
        with self.assertRaises(ArtifactError):
            validate_artifacts(self.root, self.job)

    def test_modified_card_is_rejected(self):
        generate_fake(self.root, self.job)
        path = artifact_directory(self.root, self.job)
        (path / "card-1.png").write_bytes(b"changed")
        with self.assertRaises(ArtifactError):
            validate_artifacts(self.root, self.job)

    def test_input_identity_mismatch_is_rejected(self):
        generate_fake(self.root, self.job)
        with self.assertRaises(ArtifactError):
            validate_artifacts(self.root, {**self.job, "input_sha256": "b" * 64})

    def test_manifest_cannot_read_paths_outside_the_output(self):
        generate_fake(self.root, self.job)
        path = artifact_directory(self.root, self.job)
        (path / "manifest.json").write_text(json.dumps({"artifact_version": 1, "files": {"../../secret": {}}}))
        with self.assertRaises(ArtifactError):
            validate_artifacts(self.root, self.job)

    def test_skipped_result_needs_no_image(self):
        self.job["source_url"] = "https://example.invalid/skipped/test"
        generate_fake(self.root, self.job)
        self.assertEqual(validate_artifacts(self.root, self.job)["outcome"], "skipped")
        self.assertFalse((artifact_directory(self.root, self.job) / "card-1.png").exists())

    def test_fixture_is_a_valid_png_with_intact_chunk_checksums(self):
        self.assertEqual(PIXEL[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        while offset < len(PIXEL):
            size = struct.unpack(">I", PIXEL[offset:offset + 4])[0]
            chunk = PIXEL[offset + 4:offset + 8 + size]
            checksum = struct.unpack(">I", PIXEL[offset + 8 + size:offset + 12 + size])[0]
            self.assertEqual(binascii.crc32(chunk) & 0xffffffff, checksum)
            offset += size + 12
        self.assertEqual(offset, len(PIXEL))

    def test_non_object_result_is_an_artifact_error_even_with_matching_hash(self):
        generate_fake(self.root, self.job)
        directory = artifact_directory(self.root, self.job)
        manifest = json.loads((directory / "manifest.json").read_text())
        for value in (b"[]", b"null"):
            (directory / "result.json").write_bytes(value)
            manifest["files"]["result.json"] = {"sha256": sha256(value), "bytes": len(value)}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(ArtifactError):
                validate_artifacts(self.root, self.job)


if __name__ == "__main__":
    unittest.main()
