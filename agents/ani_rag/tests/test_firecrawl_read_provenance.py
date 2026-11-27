from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from ani_rag.adapter import PageReadProvenanceError, ReadRequest
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter
from ani_rag.web_discovery import WebCandidateHit


class FirecrawlReadProvenanceTests(IsolatedAsyncioTestCase):
    url = "https://onepunchman-anime.net/character/"

    def setUp(self):
        self.adapter = FirecrawlMCPAdapter()
        self.client = Mock()
        self.client.list_tools.return_value = frozenset({"firecrawl_scrape"})
        self.adapter._clients["firecrawl"] = self.client
        self.hit = WebCandidateHit(title="CHARACTERS - TVアニメ「ワンパンマン」公式サイト", url=self.url)

    def response(self, metadata):
        self.client.call_tool.return_value = {
            "structuredContent": {"metadata": metadata, "markdown": "Character profile"},
        }

    async def test_unchanged_resource_has_an_independently_reported_final_address(self):
        for field in ("url", "finalURL", "finalUrl"):
            with self.subTest(field=field):
                final = "https://ONEPUNCHMAN-ANIME.NET:443/character/"
                self.response({"sourceURL": self.url, field: final})
                page = await self.adapter.read_web_candidate(self.hit)
                self.assertEqual(page.final_url, final)
                self.assertEqual(page.body, "Character profile")
        self.assertEqual(self.client.call_tool.call_count, 3)

    async def test_singleton_and_complete_chains_are_usable_evidence(self):
        final = self.url + "saitama"
        for chain in ([self.url], [self.url, self.url + "index/", final]):
            with self.subTest(chain=chain):
                self.response({"redirectChain": chain, "url": chain[-1]})
                page = await self.adapter.read_web_candidate(self.hit)
                self.assertEqual(page.final_url, chain[-1])

    async def test_missing_conflicting_or_incomplete_routes_do_not_return_a_page(self):
        final = self.url + "saitama"
        outside = "https://unrelated.example/character/"
        for code, fields in (
            ("redirect_missing_metadata", None),
            ("redirect_missing_final_url", {"sourceURL": self.url}),
            ("redirect_missing_final_url", {"sourceURL": self.url, "og:url": self.url}),
            ("redirect_missing_source_url", {"url": self.url}),
            ("redirect_invalid_url", {"url": False}),
            ("redirect_outside_host", {"sourceURL": self.url, "url": outside}),
            ("redirect_outside_host", {"sourceURL": self.url, "url": self.url, "finalURL": outside}),
            ("redirect_outside_host", {"redirectChain": [self.url, outside, final], "url": final}),
            ("redirect_unverified_chain", {"sourceURL": self.url, "url": self.url.rstrip("/")}),
            ("redirect_unverified_chain", {"sourceURL": self.url, "url": self.url + "?lang=ja"}),
            ("redirect_unverified_chain", {"sourceURL": self.url, "url": self.url + "#/hero/king"}),
            ("redirect_unverified_chain", {"redirectChain": [final], "url": final}),
            ("redirect_unverified_chain", {"redirectChain": [self.url, final], "url": self.url}),
            ("redirect_unverified_chain", {"redirectChain": "unknown", "sourceURL": self.url, "url": self.url}),
            ("redirect_conflicting_metadata", {"sourceURL": self.url, "url": self.url, "finalUrl": final}),
            ("redirect_conflicting_metadata", {"sourceURL": self.url, "url": self.url, "redirected": True}),
            ("redirect_conflicting_metadata", {"redirect_chain": [self.url, final], "redirectChain": [self.url], "url": self.url}),
        ):
            with self.subTest(code=code, fields=fields):
                self.response(fields)
                with self.assertRaises(PageReadProvenanceError) as caught:
                    await self.adapter.read_web_candidate(self.hit)
                self.assertEqual(caught.exception.error_code, code)
                self.assertNotIn(self.url, str(caught.exception))

    async def test_known_sources_retain_path_restrictions_on_intermediate_hops(self):
        self.response({
            "sourceURL": self.url,
            "url": self.url,
            "redirectChain": [self.url, "https://onepunchman-anime.net/shop/", self.url],
        })
        request = ReadRequest(
            source_id="official", url=self.url, mcp_server_alias="firecrawl",
            tool_name="firecrawl_scrape", allowed_hosts=frozenset({self.hit.host}),
            allowed_path_prefixes=("/character/",),
        )
        with self.assertRaises(PageReadProvenanceError) as caught:
            await self.adapter.read(request)
        self.assertEqual(caught.exception.error_code, "redirect_outside_path")
