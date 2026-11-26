"""Synthetic native envelopes exercise strict wire validation without providers."""
import base64
import copy
import hashlib
from io import BytesIO
import unittest
from uuid import uuid4

from menu_worker.rag_contract import RagContractError
from menu_worker.rag_tasks import _validate_result, publish, register_task
from test_rag_contract import request


class RagTaskContractTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.job = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()), "request": request()}
        stream = BytesIO()
        Image.new("RGB", (1308, 1632), "white").save(stream, format="PNG")
        binary = stream.getvalue()
        card = {"card_id": "card", "sha256": hashlib.sha256(binary).hexdigest(),
                "png_base64": base64.b64encode(binary).decode(), "width": 1308, "height": 1632}
        self.result = {"producer": "ani_rag", "schema_version": 1, "job_id": self.job["job_id"],
            "generation_attempt_id": self.job["generation_attempt_id"], "namespace": "team-rag", "work_id": "gw_original_42",
            "request": {"work_id": "gw_original_42", "menu_work_ids": [8], "content_type": "character",
                "query": self.job["request"]["query"], "seed_evidence_ids": ["doc_42"]},
            "source_ids": ["official", "reference"], "outcome": "ready_for_staging", "simulated": False,
            "review_mode": "llm", "producer_status": "ready_for_downstream", "review": {"decision": "pass", "issues": []},
            "usage_records": [{"stage": "review", "simulated": False, "estimated_cost_usd": "0.01"}],
            "cost_accounting": {"complete": True}, "rendered_cards": [{**card, "page": 1}, {**card, "page": 2}]}

    def test_valid_wire_result_decodes_all_original_cards(self):
        self.assertEqual(_validate_result(self.result, self.job), self.result)

    def test_fixture_pass_blocker_and_one_card_never_pass(self):
        for change in ({"simulated": True}, {"review_mode": "offline_fixture"},
                       {"review": {"decision": "pass", "issues": [{"blocking": True}]}},
                       {"rendered_cards": self.result["rendered_cards"][:1]}):
            with self.subTest(change=change), self.assertRaises(RagContractError):
                _validate_result({**self.result, **change}, self.job)

    def test_self_reported_dimensions_and_tampered_hash_are_rejected(self):
        for key, value in (("width", 100), ("sha256", "0" * 64), ("page", True)):
            result = copy.deepcopy(self.result)
            result["rendered_cards"][0][key] = value
            with self.subTest(key=key), self.assertRaises(RagContractError):
                _validate_result(result, self.job)

    def test_broker_message_is_only_job_id_and_disabled_by_default(self):
        from unittest.mock import Mock
        app = Mock()
        with self.assertRaises(RagContractError):
            publish(app, self.job["job_id"])
        app.send_task.assert_not_called()
        publish(app, self.job["job_id"], enabled=True)
        app.send_task.assert_called_once_with("content.rag_generate", args=[self.job["job_id"]], queue="content.rag_generate")

    def test_busy_delivery_waits_before_requeue(self):
        from unittest.mock import patch
        from celery.exceptions import Reject
        class App:
            def task(self, **_options):
                return lambda function: function
        task = register_task(App(), lambda: lambda _: {"reason": "generation_history_busy"})
        with patch("menu_worker.rag_tasks.time.sleep") as sleep:
            with self.assertRaises(Reject):
                task(None, self.job["job_id"])
            sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
