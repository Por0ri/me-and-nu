"""Exercise key selection over fake MCP HTTP, including the handshake for each key."""

import asyncio
import io
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ani_rag import mcp_transport
from ani_rag.adapter import ReadRequest
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter


TOOL = "firecrawl_search"
ARTICLE = "https://anime.example/episode"


def tool_error(message, status=None):
    detail = {"message": message}
    if status is not None:
        detail["statusCode"] = status
    return {"isError": True, "structuredContent": detail}


def provider_http(status, text):
    return HTTPError("https://mcp.firecrawl.dev/v2/mcp", status, "provider error", {},
                     io.BytesIO(text.encode()))


class FakeMcp:
    def __init__(self, result_for):
        self.result_for = result_for
        self.calls = []
        self.paid_calls = []
        self.tools_by_key = {}

    def open(self, request, timeout):
        assert request.full_url == "https://mcp.firecrawl.dev/v2/mcp"
        payload = json.loads(request.data)
        headers = {k.lower(): v for k, v in request.header_items()}
        key = headers.get("authorization", "").removeprefix("Bearer ")
        method = payload["method"]
        session = headers.get("mcp-session-id")
        self.calls.append((key, method, session))
        if method == "initialize":
            assert session is None, "A new credential must negotiate its own session"
            session = f"session-{key}"
            result = {"protocolVersion": "2025-03-26"}
        else:
            assert session == f"session-{key}", "A session was shared across credentials"
            if method == "notifications/initialized":
                result = None
            elif method == "tools/list":
                names = self.tools_by_key.get(key, [TOOL, "firecrawl_scrape"])
                result = {"tools": [{"name": name} for name in names]}
            else:
                assert method == "tools/call"
                self.paid_calls.append((key, payload["params"]))
                result = self.result_for(key, payload["params"])
                if isinstance(result, Exception):
                    raise result
        body = b""
        if result is not None:
            packet = {"jsonrpc": "2.0", "id": payload["id"]}
            if "jsonrpc_error" in result:
                packet["error"] = result["jsonrpc_error"]
            else:
                packet["result"] = result
            body = json.dumps(packet).encode()
        response = io.BytesIO(body)
        response.headers = {"Mcp-Session-Id": session, "Content-Type": "application/json"}
        return response


def search_result(title="Naruto"):
    return {"structuredContent": {"success": True, "data": {"web": [{"url": ARTICLE, "title": title}]}}}


@contextmanager
def isolated_adapter(environment, on_call):
    fake = FakeMcp(on_call)
    with patch.dict(os.environ, environment, clear=True), \
         patch("dotenv.load_dotenv", return_value=False), \
         patch("ani_rag.environment.load_dotenv", return_value=False), \
         patch.object(mcp_transport, "_MCP_OPENER", fake):
        yield FirecrawlMCPAdapter(), fake


def search(adapter, name="Naruto"):
    return asyncio.run(adapter.search_web(name, limit=1))


class FirecrawlKeyFailoverTests(unittest.TestCase):
    def test_missing_blank_and_repeated_slots_skip_to_next_distinct_key(self):
        env = {
            "ANI_RAG_FIRECRAWL_API_KEY": "primary",
            "Firecrawl_API_KEY2": "primary",
            "FIRECRAWL_API_KEY3": "third",
            "Firecrawl_API_KEY4": " ",
            "Firecrawl_API_KEY6": "sixth",
        }

        def outcome(key, _):
            if key == "primary":
                return provider_http(402, "Insufficient credits to perform this request")
            if key == "third":
                return tool_error("Firecrawl account has insufficient credits for this request", 402)
            return search_result()

        with isolated_adapter(env, outcome) as (adapter, fake):
            self.assertEqual(search(adapter)[0].url, ARTICLE)
            search(adapter, "next search")
            self.assertEqual([key for key, _ in fake.paid_calls], ["primary", "third", "sixth", "sixth"])
            self.assertEqual([key for key, method, _ in fake.calls if method == "initialize"],
                             ["primary", "third", "sixth"])
            self.assertEqual([key for key, method, _ in fake.calls if method == "tools/list"],
                             ["primary", "third", "sixth"])
            self.assertEqual(fake.paid_calls[0][1], fake.paid_calls[2][1])

    def test_exhausted_sixth_key_pauses_future_operations(self):
        keys = [f"slot-{number}" for number in range(1, 7)]
        env = {f"Firecrawl_API_KEY{'' if i == 1 else i}": key for i, key in enumerate(keys, 1)}
        with isolated_adapter(env, lambda *_: tool_error("Insufficient credits", 402)) as (adapter, fake):
            with self.assertRaises(RuntimeError) as exhausted:
                search(adapter)
            self.assertTrue(exhausted.exception.credits_exhausted)
            with self.assertRaises(PermissionError):
                search(adapter)
            self.assertEqual([key for key, _ in fake.paid_calls], keys)

    def test_provider_credit_error_encodings_retry_the_same_tool(self):
        env = {"Firecrawl_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        billing = {"success": False, "error": "Insufficient credits", "statusCode": 402}
        for body in (
            {"structuredContent": billing},
            {"content": [{"type": "text", "text": json.dumps(billing)}]},
            {"jsonrpc_error": {"code": -32603, "message": "Insufficient credits",
                               "data": {"status": 402}}},
        ):
            with self.subTest(body=body), isolated_adapter(
                env, lambda key, _: body if key == "primary" else search_result(),
            ) as (adapter, fake):
                self.assertEqual(search(adapter)[0].url, ARTICLE)
                self.assertEqual([key for key, _ in fake.paid_calls], ["primary", "second"])
                self.assertEqual(fake.paid_calls[0][1], fake.paid_calls[1][1])

    def test_new_credential_must_offer_the_requested_tool(self):
        env = {"Firecrawl_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        with isolated_adapter(env, lambda *_: tool_error("Insufficient credits", 402)) as (adapter, fake):
            fake.tools_by_key["second"] = ["firecrawl_scrape"]
            with self.assertRaises(PermissionError):
                search(adapter)
            self.assertEqual([key for key, _ in fake.paid_calls], ["primary"])
            self.assertIn(("second", "tools/list", "session-second"), fake.calls)

    def test_auth_rate_limit_server_and_network_failures_do_not_rotate(self):
        env = {"FIRECRAWL_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        for kind, failure in (
            ("unauthorized", provider_http(401, "bad credentials")),
            ("forbidden", provider_http(403, "access denied")),
            ("rate limit", tool_error("Rate limit exceeded", 429)),
            ("temporary", provider_http(503, "upstream unavailable")),
            ("lost connection", URLError("connection timed out")),
        ):
            with self.subTest(kind=kind), isolated_adapter(env, lambda *_: failure) as (adapter, fake):
                with self.assertRaises((RuntimeError, PermissionError, OSError)):
                    search(adapter)
                self.assertEqual([key for key, _ in fake.paid_calls], ["primary"])

    def test_tool_with_an_accepted_job_or_partial_results_is_not_replayed(self):
        env = {"Firecrawl_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        for evidence in ({"jobId": "already-queued"}, {"data": {"web": [{"url": ARTICLE}]}}):
            result = tool_error("Insufficient credits", 402)
            result["structuredContent"].update(evidence)
            with self.subTest(evidence=evidence), isolated_adapter(env, lambda *_: result) as (adapter, fake):
                with self.assertRaises(RuntimeError):
                    search(adapter)
                self.assertEqual([key for key, _ in fake.paid_calls], ["primary"])

    def test_jsonrpc_rate_limit_and_accepted_job_do_not_rotate(self):
        env = {"Firecrawl_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        for error in (
            {"code": 429, "message": "Insufficient credits"},
            {"code": 402, "message": "Insufficient credits", "data": {"jobId": "accepted-job"}},
        ):
            with self.subTest(error=error), isolated_adapter(
                env, lambda *_: {"jsonrpc_error": error},
            ) as (adapter, fake):
                with self.assertRaises(RuntimeError):
                    search(adapter)
                self.assertEqual([key for key, _ in fake.paid_calls], ["primary"])

    def test_concurrent_refusals_on_same_key_advance_exactly_one_slot(self):
        barrier = threading.Barrier(2, timeout=5)
        env = {"Firecrawl_API_KEY": "first", "Firecrawl_API_KEY2": "second", "Firecrawl_API_KEY3": "third"}

        def outcome(key, params):
            if key == "first":
                barrier.wait()
                return tool_error("Insufficient credits", 402)
            return search_result(params["arguments"]["query"])

        with isolated_adapter(env, outcome) as (_, fake):
            client = mcp_transport.StreamableHTTPMCPClient(
                mcp_transport.MCPServerEndpoint("firecrawl", "https://mcp.firecrawl.dev/v2/mcp")
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(client.call_tool, TOOL, {"query": query}) for query in ("first query", "second query")]
                responses = [future.result(timeout=10) for future in jobs]
            self.assertEqual([r["structuredContent"]["data"]["web"][0]["title"] for r in responses],
                             ["first query", "second query"])
            self.assertEqual([key for key, _ in fake.paid_calls], ["first", "first", "second", "second"])
            self.assertEqual([key for key, method, _ in fake.calls if method == "initialize"], ["first", "second"])

    def test_article_text_and_target_site_paywall_do_not_change_credentials(self):
        env = {"Firecrawl_API_KEY": "primary", "Firecrawl_API_KEY2": "second"}
        request = ReadRequest(source_id="example", url=ARTICLE, mcp_server_alias="firecrawl",
                              tool_name="firecrawl_scrape", allowed_hosts=frozenset({"anime.example"}))

        def outcome(_, params):
            if params["name"] == "firecrawl_scrape":
                return tool_error("The target website returned HTTP 402 Payment Required", 402)
            return search_result("Insufficient credits: a story about an anime studio")

        with isolated_adapter(env, outcome) as (adapter, fake):
            self.assertTrue(search(adapter)[0].title.startswith("Insufficient credits"))
            with self.assertRaises(RuntimeError):
                asyncio.run(adapter.read(request))
            self.assertEqual(search(adapter)[0].url, ARTICLE)
            self.assertEqual([key for key, _ in fake.paid_calls], ["primary"] * 3)
