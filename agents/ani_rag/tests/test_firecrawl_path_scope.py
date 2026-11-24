import unittest

from ani_rag.adapter import SearchRequest
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter, _ann_page_matches_content_type
from ani_rag.models import RagContentType


class _FakeClient:
    def __init__(self) -> None:
        self.arguments = None

    def call_tool(self, tool_name, arguments):
        assert tool_name == "firecrawl_search"
        self.arguments = arguments
        return {
            "structuredContent": {
                "success": True,
                "data": {
                    "web": [
                        {"url": "https://www.animenewsnetwork.com/news/2026-06-09/unrelated/.238324", "title": "News page"},
                        {"url": "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=1", "title": "Allowed page"},
                        {"url": "https://www.animenewsnetwork.com/encyclopedia/company.php?id=9909", "title": "Company page"},
                        {"url": "https://www.animenewsnetwork.com/encyclopedia%2F..%2Fnews/item", "title": "Encoded traversal"},
                        {"url": "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=2", "title": "Second allowed page"},
                    ]
                },
            }
        }


class FirecrawlPathScopeTests(unittest.TestCase):
    def test_search_filters_path_and_page_type_for_lane(self):
        adapter = FirecrawlMCPAdapter()
        client = _FakeClient()
        adapter._client = lambda _alias: client
        request = SearchRequest(
            source_id="ann_encyclopedia",
            query="title character",
            mcp_server_alias="firecrawl",
            tool_name="firecrawl_search",
            allowed_hosts=frozenset({"www.animenewsnetwork.com"}),
            allowed_path_prefixes=("/encyclopedia/",),
            limit=1,
            content_type=RagContentType.MUSIC,
        )

        hits = adapter._search_sync(request)

        self.assertEqual([hit.title for hit in hits], ["Allowed page"])
        self.assertEqual(client.arguments["limit"], 5)

    def test_ann_company_page_is_not_a_music_source_page(self):
        self.assertFalse(_ann_page_matches_content_type(
            "ann_encyclopedia", "https://www.animenewsnetwork.com/encyclopedia/company.php?id=9909",
            RagContentType.MUSIC,
        ))
