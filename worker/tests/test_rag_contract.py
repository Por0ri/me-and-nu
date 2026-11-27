"""RAG identities bind existing evidence, never create article URLs or allowances."""
import copy
import unittest
from uuid import uuid4

from menu_worker.rag_contract import RagContractError, RagRequest, validate_admission_receipt, validate_request


def request(**changes):
    return {"namespace": "team-rag", "work_id": "gw_original_42", "menu_work_id": 8,
            "content_type": "character", "query": "Character facts with primary citations",
            "seed_evidence": [{"document_id": "doc_42", "content_sha256": "a" * 64}],
            "allowed_source_ids": ["official", "reference"], **changes}


def receipt(value, **changes):
    item = validate_request(value)
    return {"schema_version": 1, "reservation_id": str(uuid4()), "policy_date": "2026-11-26",
            "policy_sha256": "b" * 64, "input_sha256": item.input_sha256,
            "counts_slot": True, "attempt_ordinal": 1, **changes}


class RagContractTests(unittest.TestCase):
    def test_same_seed_and_source_set_has_same_hash_without_mutable_inputs(self):
        raw = request(seed_evidence=[{"document_id": "z", "content_sha256": "c" * 64},
                                     {"document_id": "a", "content_sha256": "d" * 64}])
        item = RagRequest.parse(raw)
        alternative = copy.deepcopy(raw)
        alternative["seed_evidence"].reverse()
        alternative["allowed_source_ids"].reverse()
        self.assertEqual(item.input_sha256, validate_request(alternative).input_sha256)
        original = item.input_sha256
        raw["seed_evidence"][0]["content_sha256"] = "e" * 64
        self.assertEqual(item.input_sha256, original)
        self.assertNotEqual(item.input_sha256, validate_request(raw).input_sha256)

    def test_every_scope_change_is_bound_without_changing_existing_work_identity(self):
        original = validate_request(request())
        for changes in ({"menu_work_id": 9}, {"query": "A different topic"}, {"content_type": "guide"},
                        {"allowed_source_ids": ["official"]},
                        {"seed_evidence": [{"document_id": "doc_42", "content_sha256": "c" * 64}]}):
            changed = validate_request(request(**changes))
            self.assertEqual(changed.identity_key, original.identity_key)
            self.assertNotEqual(changed.input_sha256, original.input_sha256)
        self.assertNotEqual(validate_request(request(namespace="other")).identity_key, original.identity_key)

    def test_unregistered_execution_controls_or_period_type_are_rejected(self):
        invalid = [request(database_url="secret"), request(content_type="goods"), request(menu_work_id=True),
                   request(menu_work_id=0), request(work_id="../other"), request(query=""), request(query=" leading"),
                   request(seed_evidence=[]), request(allowed_source_ids=[]),
                   request(allowed_source_ids=["same", "same"]),
                   request(seed_evidence=[{"document_id": "doc", "content_sha256": "a" * 64}] * 2)]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(RagContractError):
                validate_request(item)

    def test_no_admission_or_wrong_binding_never_becomes_an_allowance(self):
        item = request()
        valid = receipt(item)
        self.assertEqual(validate_admission_receipt(valid, item), valid)
        for bad in (None, {}, {**valid, "counts_slot": False}, {**valid, "attempt_ordinal": 0},
                    {**valid, "attempt_ordinal": 41}, {**valid, "attempt_ordinal": True},
                    {**valid, "policy_date": "2026-11-31"}, {**valid, "input_sha256": "e" * 64},
                    {**valid, "daily_limit": 40}):
            with self.subTest(bad=bad), self.assertRaises(RagContractError):
                validate_admission_receipt(bad, item)


if __name__ == "__main__":
    unittest.main()
