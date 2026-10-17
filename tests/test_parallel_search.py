from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import websearch


class Response(io.BytesIO):
    def __init__(self, body: str, session: str = "mcp-session") -> None:
        super().__init__(body.encode())
        self.headers = Message()
        self.headers["Mcp-Session-Id"] = session
        self.headers["Content-Type"] = "application/json"


class ParallelSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = {
            "SEARCH_PROVIDER_PRIORITY": ["parallel"],
            "SEARCH_CACHE_DIR": self.temporary.name,
            "GROK_SEARCH_TIMEOUT_SECONDS": 9,
        }
        self.requests = []
        self.result = {"results": [{
            "url": "https://docs.python.org/3/library/asyncio.html",
            "title": "asyncio documentation",
            "publish_date": "2026-01-01",
            "excerpts": ["Run Python code concurrently.", "Use async and await."],
        }]}
        self.rpc_error = None
        self.tool_error = None
        self.call_failure = None
        self.sse = False

    def open_request(self, request, timeout):
        self.assertEqual(request.full_url, "https://search.parallel.ai/mcp")
        self.assertEqual(request.get_header("User-agent"), "web-search-skill/1.0")
        self.assertEqual(request.get_header("Accept"), "application/json, text/event-stream")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 9)
        payload = json.loads(request.data)
        self.requests.append(payload)
        method = payload["method"]
        if method == "initialize":
            self.assertIsNone(request.get_header("Mcp-session-id"))
            return Response(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}))
        self.assertEqual(request.get_header("Mcp-session-id"), "mcp-session")
        if method == "notifications/initialized":
            return Response("")
        self.assertEqual(method, "tools/call")
        self.assertEqual(payload["params"]["name"], "web_search")
        if self.call_failure:
            raise self.call_failure
        message = {"jsonrpc": "2.0", "id": 2}
        if self.rpc_error:
            message["error"] = self.rpc_error
        else:
            message["result"] = {"content": [{"type": "text", "text": json.dumps(self.result)}]}
            if self.tool_error:
                message["result"] = {"isError": True, "content": [{"type": "text", "text": self.tool_error}]}
        text = json.dumps(message)
        return Response(f"event: message\ndata: {text}\n\n" if self.sse else text)

    def build_opener(self, handler):
        self.assertFalse(handler.allow_internal)
        self.assertEqual(handler.timeout, 9)
        return mock.Mock(open=self.open_request)

    def run_cli(self, *args):
        output = io.StringIO()
        # No saved credentials or environment values participate in the path.
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(websearch, "load_file_config", return_value=self.config), \
                mock.patch.object(websearch.urllib.request, "build_opener", side_effect=self.build_opener), \
                mock.patch("sys.stdout", output):
            status = websearch.main(list(args))
        return status, json.loads(output.getvalue()) if output.getvalue() else None

    def test_cli_selects_anonymous_provider_and_recovers_cached_excerpts(self):
        for sse in [False, True]:
            with self.subTest(sse=sse):
                self.sse = sse
                self.requests.clear()
                status, result = self.run_cli("web_search", "--query", "Python asyncio documentation", "--format", "detailed")
                self.assertEqual(status, 0)
                self.assertEqual(result["sources_count"], 1)
                source = result["sources"][0]
                self.assertEqual(source["provider"], "parallel")
                self.assertEqual(source["title"], "asyncio documentation")
                self.assertEqual(source["published_date"], "2026-01-01")
                self.assertIn("async and await", source["content"])
                self.assertEqual(result["warnings"], [])
                self.assertEqual([r["method"] for r in self.requests], ["initialize", "notifications/initialized", "tools/call"])
                arguments = self.requests[-1]["params"]["arguments"]
                self.assertEqual(arguments, {"objective": "Python asyncio documentation", "search_queries": ["Python asyncio documentation"]})
                status, cached = self.run_cli("get_sources", "--session-id", result["session_id"])
                self.assertEqual(status, 0)
                self.assertEqual(cached["sources"], result["sources"])

    def test_opt_in_priority_doctor_and_command_scoping(self):
        default = websearch.Config({})
        self.assertEqual(websearch.search_priority_for_mode(default, "general"), ["grok", "tavily", "exa", "duckduckgo"])
        status, doctor = self.run_cli("doctor")
        self.assertEqual(status, 0)
        self.assertEqual(doctor["provider_priority"]["search"], ["parallel"])
        self.assertTrue(doctor["provider_enabled"]["parallel"])
        self.assertEqual(self.requests, [])
        probe = next(p for p in doctor["probes"] if p["name"] == "parallel-mcp")
        self.assertEqual(probe["endpoint"], "https://search.parallel.ai/mcp")
        self.assertFalse(probe["supports_recency_filter"])
        for key, default in [("FETCH_PROVIDER_PRIORITY", websearch.DEFAULT_FETCH_PROVIDER_PRIORITY), ("MAP_PROVIDER_PRIORITY", websearch.DEFAULT_MAP_PROVIDER_PRIORITY)]:
            cfg = websearch.Config({key.lower(): ["parallel"]})
            self.assertEqual(websearch.provider_priority(cfg, key, default), [])
        cfg = websearch.Config({"search_provider_priority": "parallel,exa,parallel"})
        self.assertEqual(websearch.search_priority_for_mode(cfg, "academic"), ["parallel", "exa"])

    def test_disabled_and_omitted_provider_never_calls_parallel(self):
        for priority in [[], ["unknown"], ["exa"]]:
            with self.subTest(priority=priority):
                self.config["SEARCH_PROVIDER_PRIORITY"] = priority
                with mock.patch.object(websearch, "exa_search", return_value={"sources": []}):
                    _, result = self.run_cli("web_search", "--query", "Python docs")
                self.assertEqual(result["sources_count"], 0)
                self.assertEqual(self.requests, [])

    def test_domain_filters_deduplication_source_limit_and_invalid_results(self):
        self.result["results"] = [
            {"url": "https://off-domain.org", "excerpts": ["off-domain"]},
            {"url": "https://blocked.python.org", "excerpts": ["excluded"]},
            {"url": "javascript:alert(1)"},
            None,
            *self.result["results"],
            *self.result["results"],
            {"url": "https://www.python.org", "excerpts": [3, "Python home"]},
            {"url": "https://python.org/extra", "excerpts": ["over limit"]},
        ]
        _, result = self.run_cli("web_search", "--query", "Python docs", "--include-domain", "python.org", "--exclude-domain", "blocked.python.org", "--max-sources", "2", "--format", "detailed")
        self.assertEqual([s["url"] for s in result["sources"]], ["https://docs.python.org/3/library/asyncio.html", "https://www.python.org"])
        self.assertEqual(result["sources"][1]["content"], "Python home")
        self.assertNotIn("off-domain", json.dumps(result))
        self.assertEqual(self.requests[-1]["params"]["arguments"]["search_queries"], ["site:python.org Python docs -site:blocked.python.org"])

    def test_freshness_skips_network_and_uses_next_configured_provider(self):
        self.config["SEARCH_PROVIDER_PRIORITY"] = ["parallel", "exa"]
        fallback = {"sources": [{"url": "https://example.com/news", "provider": "exa"}]}
        for extra in [["--recency-days", "3"], ["--mode", "news"]]:
            with self.subTest(extra=extra), mock.patch.object(websearch, "exa_search", return_value=fallback) as exa:
                _, result = self.run_cli("web_search", "--query", "Python news", *extra)
                self.assertEqual(result["sources"][0]["provider"], "exa")
                self.assertIn("recency filter", result["warnings"][0])
                self.assertEqual(exa.call_args.kwargs["recency_days"], 3 if extra[0] == "--recency-days" else 7)
                self.assertEqual(self.requests, [])

    def test_failures_empty_and_malformed_results_fall_back(self):
        self.config["SEARCH_PROVIDER_PRIORITY"] = ["parallel", "exa"]
        fallback = {"sources": [{"url": "https://example.com/fallback", "provider": "exa"}]}
        cases = [
            ("call_failure", TimeoutError("request timed out")),
            ("call_failure", urllib.error.URLError("offline")),
            ("call_failure", urllib.error.HTTPError(websearch.PARALLEL_MCP_URL, 429, "Too Many Requests", {}, io.BytesIO(b"quota reached"))),
            ("rpc_error", {"code": -32000, "message": "service error"}),
            ("tool_error", "tool failed"),
            ("result", {"results": []}),
            ("result", {"results": "invalid"}),
            ("result", "not an object"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=str(value)), mock.patch.object(websearch, "exa_search", return_value=fallback):
                previous = getattr(self, field)
                setattr(self, field, value)
                try:
                    _, result = self.run_cli("web_search", "--query", "Python docs")
                finally:
                    setattr(self, field, previous)
                self.assertEqual(result["sources"][0]["provider"], "exa")
                self.assertTrue(result["warnings"])

    def test_keyboard_interrupt_stops_cli_without_fallback_or_cache(self):
        self.config["SEARCH_PROVIDER_PRIORITY"] = ["parallel", "exa"]
        self.call_failure = KeyboardInterrupt()
        with mock.patch.object(websearch, "exa_search") as exa:
            status, result = self.run_cli("web_search", "--query", "Python docs")
        self.assertEqual(status, 130)
        self.assertIsNone(result)
        exa.assert_not_called()
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
