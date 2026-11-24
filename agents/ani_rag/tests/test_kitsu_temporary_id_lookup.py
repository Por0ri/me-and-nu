import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter


def anime(identifier, canonical, alternative):
    return {"type": "anime", "id": identifier, "attributes": {
        "canonicalTitle": canonical, "titles": {"en_us": alternative},
    }}


class KitsuTemporaryIdLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.adapter = FirecrawlMCPAdapter()
        self.adapter._clients["firecrawl"] = self.client

    def respond(self, payload):
        self.client.call_tool.return_value = {"structuredContent": {"markdown": json.dumps(payload)}}

    async def test_exact_alternate_titles_only(self):
        self.respond({"data": [anime("50361", "Other Film", "Other"),
                               anime("50362", "Seiza", "Starlit Parade"),
                               anime("50363", "Star:lit Parade", "Alternate")]})
        self.assertEqual(await self.adapter.lookup_kitsu_title("starlit parade"), ("50362", "50363"))
        tool, arguments = self.client.call_tool.call_args.args
        self.assertEqual(tool, "firecrawl_scrape")
        url = urlsplit(arguments["url"])
        self.assertEqual((url.hostname, url.path), ("kitsu.io", "/api/edge/anime"))
        self.assertEqual(parse_qs(url.query)["filter[text]"], ["starlit parade"])
        self.client.call_tool.assert_called_once()

    async def test_successful_empty_and_nonmatching_results(self):
        for data in ([], [anime("50", "Similar Story", "Another Story")]):
            self.respond({"data": data})
            self.assertEqual(await self.adapter.lookup_kitsu_title("Starlit Parade"), ())

    async def test_partial_or_invalid_results_are_not_proof_of_absence(self):
        for payload in (
            {"data": [], "links": {"next": "https://kitsu.io/api/edge/anime?page[offset]=20"}},
            {"data": [], "meta": {"count": 5}},
            {"data": None}, {"errors": [{}]}, {"data": [{"type": "anime", "id": "50362"}]},
        ):
            with self.subTest(payload=payload):
                self.respond(payload)
                with self.assertRaises(RuntimeError):
                    await self.adapter.lookup_kitsu_title("Starlit Parade")
