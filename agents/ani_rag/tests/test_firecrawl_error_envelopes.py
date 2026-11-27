import json
import unittest
from unittest.mock import Mock

from ani_rag.adapter import ReadRequest, SearchRequest
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter, MCPToolCallError
from ani_rag.firecrawl_errors import is_credit_refusal, tool_credit_refusal


def text_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class FirecrawlErrorEnvelopeTests(unittest.TestCase):
    def test_unsupported_site_403_leaves_other_websites_available(self):
        refusal = "Firecrawl can't scrape this site because it is currently not supported."
        for message, should_pause in (
            (refusal, False),
            ("Invalid API key.", True),
            (refusal + " Authentication required.", True),
        ):
            with self.subTest(message=message):
                adapter = FirecrawlMCPAdapter()
                error = MCPToolCallError("read", {"isError": True, "structuredContent": {
                    "success": False, "error": {"statusCode": 403, "message": message},
                }}, requested_url="https://article.example/story")
                self.assertEqual(error.unsupported_site, not should_pause)
                self.assertEqual(error.operator_action_required, should_pause)
                adapter._pause_firecrawl_if_needed("firecrawl", error)
                if should_pause:
                    with self.assertRaises(PermissionError):
                        adapter._ensure_firecrawl_active("firecrawl")
                else:
                    self.assertIn("unsupported_target", error.safe_summary)
                    adapter._ensure_firecrawl_active("firecrawl")

    def test_all_paid_tool_surfaces_preserve_the_rejection(self):
        hosts = frozenset({"article.example"})
        read = ReadRequest("official", "https://article.example/news", "firecrawl", "firecrawl_scrape", hosts)
        search = SearchRequest("official", "title", "firecrawl", "firecrawl_search", hosts)
        kitsu = SearchRequest("kitsu_anime_catalog", "title", "firecrawl", "firecrawl_scrape", frozenset({"kitsu.io"}))
        adapter = FirecrawlMCPAdapter()
        client = Mock()
        adapter._client = Mock(return_value=client)
        rejected = {"success": False, "error": {"statusCode": 402, "code": "INSUFFICIENT_CREDITS"}}
        for envelope in (
            {"isError": True, "content": [{"type": "text", "text": "Insufficient credits to perform this request"}]},
            {"structuredContent": rejected}, text_result(rejected),
        ):
            client.call_tool.return_value = envelope
            for operation, call, args in (
                ("read", adapter._read_sync, (read,)), ("search", adapter._search_sync, (search,)),
                ("web_discovery_search", adapter._search_web_sync, ("title", 1)),
                ("web_image_search", adapter._search_images_sync, ("image", 1)),
                ("kitsu_api_search", adapter._search_sync, (kitsu,)),
            ):
                with self.subTest(operation=operation, envelope=envelope), self.assertRaises(MCPToolCallError) as raised:
                    call(*args)
                self.assertTrue(raised.exception.credits_exhausted)
                self.assertIn(f"operation={operation}", raised.exception.safe_summary)

    def test_successful_page_and_other_provider_failures_never_signal_depletion(self):
        message = "Insufficient credits"
        for fields in (
            {"success": True, "markdown": message},
            {"metadata": {"statusCode": 402, "sourceURL": "https://article.example"}, "markdown": message},
            {"success": False, "error": "Payment Required", "statusCode": 402},
            {"success": False, "error": "Target site returned HTTP 402: " + message},
            {"success": False, "error": "Could not read https://article.example/insufficient-credits"},
            {"success": False, "statusCode": 400, "error": {"statusCode": 429, "message": message}},
            {"success": False, "error": message, "data": {"jobId": "in-flight"}},
            {"success": False, "error": message, "scrape_id": "in-flight"},
            {"success": False, "error": message, "data": {"web": [{"url": "https://article.example/news"}]}},
            {"success": False, "error": message, "metadata": {"statusCode": 402}},
            *({"success": False, "error": {"message": message, "statusCode": code}} for code in (401, 403, 429, 503)),
        ):
            with self.subTest(fields=fields):
                self.assertFalse(tool_credit_refusal({"structuredContent": fields}))
                self.assertFalse(tool_credit_refusal(text_result(fields)))

    def test_only_explicit_unambiguous_credit_failure_replays(self):
        for detail in ("CREDITS_EXHAUSTED", "You don't have enough credits", "Your credit balance is 0."):
            self.assertTrue(is_credit_refusal(402, detail))
        for detail in ("Credit balance is 0.75", "Quota exceeded", "Too many requests: insufficient credits",
                       "Insufficient credits, jobId=accepted-12", "No credits were charged"):
            self.assertFalse(is_credit_refusal(None, detail))

    def test_logged_error_strips_credential_and_url(self):
        fields = {"success": False, "error": "Insufficient credits for fc-placeholder-demo at https://article.example"}
        error = MCPToolCallError("read", text_result(fields))
        self.assertTrue(error.credits_exhausted)
        self.assertNotIn("placeholder-demo", str(error))
        self.assertNotIn("article.example", str(error))
