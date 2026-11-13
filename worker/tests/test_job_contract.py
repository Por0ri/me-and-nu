"""Fixture input identity and rejection of non-fixture execution inputs."""
import unittest

from menu_worker.job_contract import JobInput, JobInputError, validate_input


class JobContractTests(unittest.TestCase):
    def request(self, **overrides):
        return {"idempotency_key": "contract-1", "source_id": "fixture",
                "source_url": "https://example.invalid/success/one", "content_type": "news", **overrides}

    def test_idempotency_uses_canonical_input_without_tracking_query(self):
        first = validate_input(self.request(source_url="https://example.invalid:443/success/one?b=2&utm_source=test&a=1"))
        second = validate_input(self.request(source_url="https://example.invalid/success/one?a=1&b=2"))
        self.assertEqual(first.canonical_url, second.canonical_url)
        self.assertEqual(first.input_sha256, second.input_sha256)
        self.assertNotEqual(first.source_url, second.source_url)

    def test_type_changes_payload_identity_but_not_candidate_identity(self):
        first = validate_input(self.request())
        second = validate_input(self.request(content_type="review"))
        self.assertEqual(first.canonical_url, second.canonical_url)
        self.assertNotEqual(first.input_sha256, second.input_sha256)

    def test_execution_settings_cannot_be_smuggled_into_input(self):
        for name in ("database_url", "agent_root", "review_mode", "mode", "credentials_file", "job_kind"):
            with self.subTest(name=name), self.assertRaises(JobInputError):
                validate_input(self.request(**{name: "untrusted"}))

    def test_v1_hash_still_matches_existing_fake_manifests(self):
        request = validate_input(self.request())
        v1_hash = "d60a72458c4d08da9f63c5bcfa906fe89c9cfb2b79e372745c2c33e01eaee55e"
        self.assertEqual(request.input_sha256, v1_hash)
        self.assertEqual(request.hash_for_kind("fake"), v1_hash)
        with self.assertRaises(JobInputError):
            request.hash_for_kind("anime")

    def test_fixture_cannot_enter_anime_queue(self):
        for scenario in ("success", "held", "skipped", "fail", "slow"):
            request = validate_input(self.request(source_url=f"https://example.invalid/{scenario}/one"))
            with self.subTest(scenario=scenario), self.assertRaises(JobInputError):
                request.hash_for_kind("anime")

    def test_external_or_ambiguous_urls_are_rejected(self):
        for value in ("https://example.org/success/one", "http://example.invalid/success/one",
                      "https://user:pass@example.invalid/success/one", "https://example.invalid:8443/success/one",
                      "https://example.invalid/success/one#fragment", "https://example.invalid/success/../fail/one",
                      "https://example.invalid/unknown/one", "https://example.invalid/success/", "https://example.invalid/success/one\n"):
            with self.subTest(url=value), self.assertRaises(JobInputError):
                validate_input(self.request(source_url=value))

    def test_all_fixture_scenarios_and_known_types_are_supported(self):
        for scenario in ("success", "held", "skipped", "fail", "slow"):
            self.assertEqual(validate_input(self.request(source_url=f"https://example.invalid/{scenario}/one")).scenario, scenario)
        for kind in ("news", "event", "guide", "people", "goods", "review", "humor", "character", "curation", "music"):
            self.assertEqual(validate_input(self.request(content_type=kind)).content_type, kind)

    def test_direct_dataclass_construction_cannot_bypass_validation(self):
        with self.assertRaises(JobInputError):
            JobInput(**self.request(source_id="official"))
        with self.assertRaises(JobInputError):
            JobInput(**self.request(content_type="behind"))
        with self.assertRaises(JobInputError):
            JobInput(**self.request(idempotency_key=" "))


if __name__ == "__main__":
    unittest.main()
