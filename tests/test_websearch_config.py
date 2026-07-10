from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.parse
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import websearch  # noqa: E402


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_duckduckgo_html_backend(self) -> None:
        cfg = websearch.Config({})
        output = io.StringIO()

        with mock.patch("sys.stdout", output):
            websearch.command_doctor(mock.Mock(), cfg)

        payload = json.loads(output.getvalue())
        probes = payload["probes"]
        probe_names = [probe["name"] for probe in probes]
        duckduckgo_probe = next(probe for probe in probes if probe["name"] == "duckduckgo-html")

        self.assertNotIn("duckduckgo-instant-answer", probe_names)
        self.assertEqual(duckduckgo_probe["endpoint"], websearch.DUCKDUCKGO_HTML_URL)
        self.assertEqual(duckduckgo_probe["auth"], "no-key")
        self.assertTrue(duckduckgo_probe["supports_domain_filter"])
        self.assertTrue(duckduckgo_probe["supports_recency_filter"])
        self.assertEqual(
            duckduckgo_probe["instant_answer_fallback_endpoint"],
            websearch.DUCKDUCKGO_INSTANT_ANSWER_URL,
        )

    def test_doctor_redacts_credentials_and_sensitive_endpoint_query_values(self) -> None:
        cfg = websearch.Config(
            {
                "grok_search_upstreams": [
                    {
                        "grok_search_api_key": "grok-key",
                        "grok_search_model": "model",
                        "grok_search_url": "https://user:password@ai.example/v1",
                    }
                ],
                "tavily_upstreams": [
                    {
                        "tavily_api_key": "tavily-key",
                        "tavily_api_url": (
                            "https://user:password@tavily.example/api?token=secret&apiKey=secret2"
                            "&authToken=secret3&clientSecret=secret4&sessionToken=secret5"
                            "&refreshToken=secret6&apiToken=secret7&idToken=secret8&csrfToken=secret9"
                            "&sessionId=secret10&credential=secret11&jwt=secret12&cookie=secret13"
                            "&pass-word=secret14"
                            "&design=visible&region=us#fragment"
                        ),
                    }
                ],
                "firecrawl_upstreams": [
                    {
                        "firecrawl_api_key": "firecrawl-key",
                        "firecrawl_api_url": "https://firecrawl.example/api?auth_key=secret",
                    }
                ],
            }
        )
        output = io.StringIO()

        with mock.patch("sys.stdout", output):
            websearch.command_doctor(mock.Mock(), cfg)

        probes = {probe["name"]: probe for probe in json.loads(output.getvalue())["probes"]}
        probe_text = json.dumps(probes)
        self.assertNotIn("user", probe_text)
        self.assertNotIn("password", probe_text)
        self.assertNotIn("secret", probe_text)
        self.assertNotIn("fragment", probe_text)
        self.assertEqual(probes["ai-provider"]["api_url"], "https://ai.example/v1")
        tavily_url = urllib.parse.urlsplit(probes["tavily"]["endpoint"])
        self.assertEqual(tavily_url.netloc, "tavily.example")
        self.assertEqual(
            urllib.parse.parse_qs(tavily_url.query),
            {
                "token": ["[redacted]"],
                "apiKey": ["[redacted]"],
                "authToken": ["[redacted]"],
                "clientSecret": ["[redacted]"],
                "sessionToken": ["[redacted]"],
                "refreshToken": ["[redacted]"],
                "apiToken": ["[redacted]"],
                "idToken": ["[redacted]"],
                "csrfToken": ["[redacted]"],
                "sessionId": ["[redacted]"],
                "credential": ["[redacted]"],
                "jwt": ["[redacted]"],
                "cookie": ["[redacted]"],
                "pass-word": ["[redacted]"],
                "design": ["visible"],
                "region": ["us"],
            },
        )
        firecrawl_url = urllib.parse.urlsplit(probes["firecrawl"]["endpoint"])
        self.assertEqual(urllib.parse.parse_qs(firecrawl_url.query), {"auth_key": ["[redacted]"]})

    def test_warning_redaction_covers_auth_and_encryption_keys(self) -> None:
        auth_key = "auth-value-must-not-leak"
        encryption_key = "encryption-value-must-not-leak"
        cfg = websearch.Config(
            {
                "AUTH_KEY": auth_key,
                "ENCRYPTION_KEY": encryption_key,
                "tavily_api_url": (
                    "https://encoded%2Buser:encoded%2Bpassword@example.com/api?sessionToken=url-secret"
                    "&encodedToken=a%2Bb+c&region=us"
                ),
            }
        )

        warnings = websearch.safe_warnings(
            [
                f"failed with {auth_key} and {encryption_key}",
                "endpoint sessionToken=url-secret encodedToken=a%2Bb+c region=us",
                "decoded a+b c percent-space a%2Bb%20c region=us",
                "userinfo encoded%2Buser encoded%2Bpassword decoded encoded+user encoded+password",
            ],
            cfg,
        )

        self.assertEqual(
            warnings,
            [
                "failed with [redacted] and [redacted]",
                "endpoint sessionToken=[redacted] encodedToken=[redacted] region=us",
                "decoded [redacted] percent-space [redacted] region=us",
                "userinfo [redacted] [redacted] decoded [redacted] [redacted]",
            ],
        )

    def test_warning_redaction_bounds_short_auth_and_encryption_keys(self) -> None:
        cfg = websearch.Config({"AUTH_KEY": "x", "ENCRYPTION_KEY": "yz"})

        warnings = websearch.safe_warnings(["auth=x encryption=yz keep=axylophone"], cfg)

        self.assertEqual(warnings, ["auth=[redacted] encryption=[redacted] keep=axylophone"])

    def test_warning_redaction_covers_non_string_sensitive_values(self) -> None:
        cfg = websearch.Config({"AUTH_KEY": 123, "ENCRYPTION_KEY": 456})

        warnings = websearch.safe_warnings(["auth=123 encryption=456 keep=1234"], cfg)

        self.assertEqual(warnings, ["auth=[redacted] encryption=[redacted] keep=1234"])


class ConfigBomTests(unittest.TestCase):
    def test_normalize_utf8_bom_config_rewrites_file_on_current_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            normalized = b'GROK_SEARCH_MODEL = "model"\n'
            config_path.write_bytes(websearch.UTF8_BOM + normalized)

            self.assertEqual(
                websearch.normalize_utf8_bom_config(config_path, config_path.read_bytes()),
                normalized,
            )

            self.assertEqual(config_path.read_bytes(), normalized)

    def test_normalize_utf8_bom_config_preserves_inode_and_mode(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX inode and mode behavior")
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            normalized = b'GROK_SEARCH_MODEL = "model"\n'
            config_path.write_bytes(websearch.UTF8_BOM + normalized)
            os.chmod(config_path, 0o600)
            before = config_path.stat()

            websearch.normalize_utf8_bom_config(config_path, config_path.read_bytes())

            after = config_path.stat()
            self.assertEqual(after.st_ino, before.st_ino)
            self.assertEqual(after.st_uid, before.st_uid)
            self.assertEqual(after.st_gid, before.st_gid)
            self.assertEqual(after.st_mode & 0o777, 0o600)
            self.assertEqual(config_path.read_bytes(), normalized)

    def test_normalize_utf8_bom_config_does_not_create_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            normalized = b'GROK_SEARCH_MODEL = "model"\n'
            config_path.write_bytes(websearch.UTF8_BOM + normalized)

            websearch.normalize_utf8_bom_config(config_path, config_path.read_bytes())

            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_normalize_utf8_bom_config_failure_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            normalized = b'GROK_SEARCH_MODEL = "model"\n'
            config_path.write_bytes(websearch.UTF8_BOM + normalized)
            raw = config_path.read_bytes()

            with (
                mock.patch.object(Path, "open", side_effect=PermissionError("denied")),
                self.assertRaises(websearch.ConfigError),
            ):
                websearch.normalize_utf8_bom_config(config_path, raw)

            self.assertEqual(config_path.read_bytes(), websearch.UTF8_BOM + normalized)
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_read_config_file_parses_bom_config_when_normalization_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            normalized = b'GROK_SEARCH_MODEL = "model"\n'
            config_path.write_bytes(websearch.UTF8_BOM + normalized)

            with mock.patch.object(
                websearch,
                "normalize_utf8_bom_config",
                side_effect=websearch.ConfigError("denied"),
            ):
                values = websearch.read_config_file(config_path)

            self.assertEqual(values["GROK_SEARCH_MODEL"], "model")
            self.assertEqual(config_path.read_bytes(), websearch.UTF8_BOM + normalized)


class SearchFallbackTests(unittest.TestCase):
    def test_search_result_status_reports_skip_reason(self) -> None:
        self.assertEqual(
            websearch.search_result_status({"skip_reason": "does not support recency filters"}),
            "does not support recency filters",
        )

    def test_duckduckgo_search_parses_html_results_and_decodes_redirect(self) -> None:
        cfg = websearch.Config({})
        html = """
        <html><body>
          <div class="result">
            <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fx%3D1%26q%3Da%252Bb">
              Example &amp; Result
            </a>
            <a class="result__snippet">Snippet <b>text</b></a>
          </div>
        </body></html>
        """

        with mock.patch.object(websearch, "request_text", return_value=html) as request_text:
            result = websearch.duckduckgo_search(
                cfg,
                "example",
                max_sources=5,
                detailed=False,
                include_domains=[],
                exclude_domains=[],
                recency_days=None,
            )

        request_text.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "duckduckgo")
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["title"], "Example & Result")
        self.assertEqual(result["sources"][0]["url"], "https://example.com/page?x=1&q=a%2Bb")
        self.assertEqual(result["sources"][0]["content"], "Snippet text")

    def test_duckduckgo_search_applies_domain_and_recency_to_html_backend(self) -> None:
        cfg = websearch.Config({})
        html = """
        <html><body>
          <div class="result">
            <a class="result__a" href="https://example.com/kept">Kept</a>
            <a class="result__snippet">Kept snippet</a>
          </div>
          <div class="result">
            <a class="result__a" href="https://blocked.com/dropped">Dropped</a>
            <a class="result__snippet">Dropped snippet</a>
          </div>
        </body></html>
        """

        with mock.patch.object(websearch, "request_text", return_value=html) as request_text:
            result = websearch.duckduckgo_search(
                cfg,
                "python",
                max_sources=5,
                detailed=False,
                include_domains=["example.com"],
                exclude_domains=["blocked.com"],
                recency_days=7,
            )

        requested_url = request_text.call_args.args[1]
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(requested_url).query))
        self.assertEqual(params["df"], "w")
        self.assertEqual(params["q"], "site:example.com python")
        self.assertEqual([source["url"] for source in result["sources"]], ["https://example.com/kept"])

    def test_duckduckgo_filtering_does_not_hide_later_valid_results(self) -> None:
        blocked = "".join(
            f'<a class="result__a" href="https://blocked.test/{index}">Blocked</a>'
            for index in range(websearch.MAX_SEARCH_SOURCES)
        )
        html = blocked + '<a class="result__a" href="https://example.com/valid">Valid result</a>'

        sources = websearch.duckduckgo_parse_html_sources(
            html,
            max_sources=2,
            include_domains=["example.com"],
            exclude_domains=[],
        )

        self.assertEqual([source["url"] for source in sources], ["https://example.com/valid"])

    def test_duckduckgo_search_uses_instant_answer_as_error_candidate(self) -> None:
        cfg = websearch.Config({})

        with (
            mock.patch.object(websearch, "request_text", side_effect=websearch.HttpError(429, "rate limited")),
            mock.patch.object(
                websearch,
                "request_json",
                return_value={
                    "AbstractText": "Instant fallback",
                    "AbstractURL": "https://example.org/instant",
                    "Heading": "Fallback",
                    "RelatedTopics": [],
                },
            ) as request_json,
        ):
            result = websearch.duckduckgo_search(
                cfg,
                "example",
                max_sources=5,
                detailed=False,
                include_domains=[],
                exclude_domains=[],
                recency_days=None,
            )

        request_json.assert_called_once()
        self.assertEqual(result["answer"], "Instant fallback")
        self.assertEqual([source["url"] for source in result["sources"]], ["https://example.org/instant"])

    def test_duckduckgo_search_clears_instant_answer_when_answer_source_is_filtered(self) -> None:
        cfg = websearch.Config({})

        with (
            mock.patch.object(websearch, "request_text", side_effect=websearch.HttpError(429, "rate limited")),
            mock.patch.object(
                websearch,
                "request_json",
                return_value={
                    "AbstractText": "Off-domain answer",
                    "AbstractURL": "https://off-domain.example/instant",
                    "Heading": "Filtered",
                    "RelatedTopics": [
                        {
                            "Text": "Allowed topic - Allowed snippet",
                            "FirstURL": "https://example.org/allowed",
                        },
                    ],
                },
            ),
        ):
            result = websearch.duckduckgo_search(
                cfg,
                "example",
                max_sources=5,
                detailed=False,
                include_domains=["example.org"],
                exclude_domains=[],
                recency_days=None,
            )

        self.assertEqual(result["answer"], "")
        self.assertEqual([source["url"] for source in result["sources"]], ["https://example.org/allowed"])

    def test_duckduckgo_search_does_not_use_instant_answer_for_recency_failure(self) -> None:
        cfg = websearch.Config({})

        with (
            mock.patch.object(websearch, "request_text", side_effect=websearch.HttpError(429, "rate limited")),
            mock.patch.object(websearch, "request_json") as request_json,
        ):
            result = websearch.duckduckgo_search(
                cfg,
                "example",
                max_sources=5,
                detailed=False,
                include_domains=[],
                exclude_domains=[],
                recency_days=7,
            )

        request_json.assert_not_called()
        self.assertEqual(result["sources"], [])
        self.assertIn("html search failed", result["skip_reason"])


class WebsearchRegressionTests(unittest.TestCase):
    @staticmethod
    def search_args(**overrides: object) -> mock.Mock:
        values: dict[str, object] = {
            "mode": "general",
            "format": "detailed",
            "max_chars": 100,
            "grok_max_retries": None,
            "recency_days": None,
            "query": "example query",
            "max_sources": 5,
            "include_domain": [],
            "exclude_domain": [],
        }
        values.update(overrides)
        return mock.Mock(**values)

    def test_strip_html_ignores_unclosed_script_content(self) -> None:
        html = "<p>visible</p><script>" + ("x" * 1000)

        self.assertEqual(websearch.strip_html(html), "visible")

    def test_strip_html_caps_many_short_text_nodes(self) -> None:
        html = "<b>xy</b>" * 10_000

        result = websearch.strip_html(html, max_chars=100)

        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.startswith("xy xy"))

    def test_strip_html_caps_empty_tag_events(self) -> None:
        html = "<b></b>" * 10_000

        result = websearch.strip_html(html, max_chars=100, max_events=100)

        self.assertEqual(result, "")

    def test_plain_html_parser_counts_comment_events(self) -> None:
        parser = websearch._PlainTextHTMLParser(max_chars=100, max_events=10)

        with self.assertRaises(websearch._HTMLTextLimitReached):
            parser.feed("<!--x-->" * 100)

    def test_ai_search_does_not_treat_answer_url_as_a_source(self) -> None:
        cfg = websearch.Config({})
        response = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Unsupported claim",
                                "url": "https://attacker.example/source",
                            }
                        ],
                    }
                }
            ]
        }

        with (
            mock.patch.object(
                websearch,
                "random_upstream",
                return_value={
                    "grok_search_api_key": "key",
                    "grok_search_url": "https://api.example.com",
                    "grok_search_model": "model",
                },
            ),
            mock.patch.object(websearch, "request_json", return_value=response),
        ):
            result = websearch.ai_search(cfg, "query")

        self.assertEqual(result["urls"], [])

    def test_ai_search_accepts_structured_citation_urls(self) -> None:
        cfg = websearch.Config({})
        response = {
            "choices": [{"message": {"content": "Supported claim"}}],
            "citations": ["https://example.com/source"],
        }

        with (
            mock.patch.object(
                websearch,
                "random_upstream",
                return_value={
                    "grok_search_api_key": "key",
                    "grok_search_url": "https://api.example.com",
                    "grok_search_model": "model",
                },
            ),
            mock.patch.object(websearch, "request_json", return_value=response),
        ):
            result = websearch.ai_search(cfg, "query")

        self.assertEqual(result["urls"], ["https://example.com/source"])

    def test_ai_search_limits_structured_citations(self) -> None:
        cfg = websearch.Config({})
        response = {
            "choices": [{"message": {"content": "Supported claim"}}],
            "citations": [f"https://example.com/{index}" for index in range(100)],
        }

        with (
            mock.patch.object(
                websearch,
                "random_upstream",
                return_value={
                    "grok_search_api_key": "key",
                    "grok_search_url": "https://api.example.com",
                    "grok_search_model": "model",
                },
            ),
            mock.patch.object(websearch, "request_json", return_value=response),
        ):
            result = websearch.ai_search(cfg, "query", max_sources=3)

        self.assertEqual(result["urls"], [f"https://example.com/{index}" for index in range(3)])

    def test_exa_plain_url_fallback_limits_sources(self) -> None:
        text = " ".join(f"https://example.com/{index}" for index in range(100))

        sources = websearch.exa_sources_from_mcp_text(text, max_sources=3)

        self.assertEqual(len(sources), 3)

    def test_exa_text_fallback_does_not_materialize_all_lines(self) -> None:
        class NoSplitlines(str):
            def splitlines(self, *args, **kwargs):
                raise AssertionError("Exa text fallback must stream lines")

        text = NoSplitlines("Title: Example\nURL: https://example.com/source\nBody")

        sources = websearch.exa_sources_from_mcp_text(text, max_sources=1)

        self.assertEqual([source["url"] for source in sources], ["https://example.com/source"])

    def test_exa_json_results_stop_processing_at_source_limit(self) -> None:
        class UnexpectedResult(dict):
            def get(self, key, default=None):
                raise AssertionError(f"result beyond source limit was processed: {key}")

        data = {
            "results": [
                {"title": "First", "url": "https://example.com/first", "text": "content"},
                UnexpectedResult(),
            ]
        }

        with mock.patch.object(websearch.json, "loads", return_value=data):
            sources = websearch.exa_sources_from_mcp_text("{}", max_sources=1)

        self.assertEqual([source["url"] for source in sources], ["https://example.com/first"])

    def test_jsonrpc_sse_returns_target_without_parsing_later_messages(self) -> None:
        class NoRepeatedFind(str):
            def find(self, *args, **kwargs):
                raise AssertionError("SSE line scanning must remain linear")

        text = NoRepeatedFind(
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
        )

        with (
            mock.patch.object(websearch.json, "loads", wraps=json.loads) as loads,
            mock.patch.object(
                websearch.io,
                "StringIO",
                side_effect=AssertionError("SSE parsing must not copy the full response"),
            ),
        ):
            message = websearch.first_jsonrpc_message(text, 1)

        self.assertEqual(message["id"], 1)
        self.assertEqual(loads.call_count, 1)

    def test_sse_parser_rejects_an_event_with_excessive_data_lines(self) -> None:
        max_event_lines = 10_000
        text = "data: []\n" * (max_event_lines + 1)

        with mock.patch.object(
            websearch.json,
            "loads",
            side_effect=AssertionError("oversized SSE event must not be parsed"),
        ):
            messages = list(websearch.parse_sse_json_messages(text))

        self.assertEqual(messages, [])

    def test_sse_parser_accepts_carriage_return_line_endings(self) -> None:
        text = (
            'data: {"jsonrpc":"2.0","id":1}\r\r'
            'data: {"jsonrpc":"2.0","id":2}\r\r'
        )

        messages = list(websearch.parse_sse_json_messages(text))

        self.assertEqual([message["id"] for message in messages], [1, 2])

    def test_find_urls_streams_until_the_limit(self) -> None:
        text = " ".join(f"https://example.com/{index}" for index in range(100))

        with mock.patch.object(websearch.re, "findall", side_effect=AssertionError("must stream")):
            urls = websearch.find_urls({"text": text}, max_urls=3)

        self.assertEqual(urls, [f"https://example.com/{index}" for index in range(3)])

    def test_find_urls_bounds_duplicate_matches(self) -> None:
        text = "https://example.com/source " * 100_000

        with mock.patch.object(
            websearch,
            "normalize_source_url",
            wraps=websearch.normalize_source_url,
        ) as normalize_source_url:
            urls = websearch.find_urls({"text": text}, max_urls=5)

        self.assertEqual(urls, ["https://example.com/source"])
        self.assertLessEqual(normalize_source_url.call_count, 100)

    def test_duplicate_urls_do_not_hide_a_later_unique_url(self) -> None:
        repeated = "https://example.com/repeated " * 100
        text = repeated + "https://example.com/unique"

        self.assertEqual(
            websearch.find_urls({"text": text}, max_urls=2),
            ["https://example.com/repeated", "https://example.com/unique"],
        )
        self.assertEqual(
            websearch.find_structured_urls(
                {"citations": ["https://example.com/repeated"] * 100 + ["https://example.com/unique"]},
                max_urls=2,
            ),
            ["https://example.com/repeated", "https://example.com/unique"],
        )

        invalid_urls = [f"http:///invalid-{index}" for index in range(1000)]
        self.assertEqual(
            websearch.find_urls({"text": " ".join(invalid_urls + ["https://example.com/valid"])}, max_urls=1),
            ["https://example.com/valid"],
        )
        self.assertEqual(
            websearch.find_structured_urls(
                {"citations": invalid_urls + ["https://example.com/valid"]},
                max_urls=1,
            ),
            ["https://example.com/valid"],
        )

    def test_search_falls_back_when_ai_answer_has_no_sources(self) -> None:
        cfg = websearch.Config({"search_provider_priority": ["grok", "tavily"]})
        args = self.search_args()
        tavily_result = {
            "answer": "fallback answer",
            "sources": [{"title": "Example", "url": "https://example.com"}],
        }

        with (
            mock.patch.object(websearch, "ai_search", return_value={"answer": "uncited", "sources": []}),
            mock.patch.object(websearch, "tavily_search", return_value=tavily_result) as tavily_search,
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef"),
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_search(args, cfg)

        tavily_search.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["sources_count"], 1)
        self.assertIn("answer without sources", " ".join(payload["warnings"]))

    def test_search_falls_back_when_all_source_urls_are_invalid(self) -> None:
        cfg = websearch.Config({"search_provider_priority": ["grok", "tavily"]})
        args = self.search_args()
        tavily_result = {
            "answer": "fallback answer",
            "sources": [{"title": "Example", "url": "https://example.com"}],
        }
        invalid_result = {
            "answer": "uncited",
            "sources": [
                {"title": "Invalid", "url": "javascript:alert(1)"},
                {"title": "Too long", "url": "https://example.com/" + ("x" * 3000)},
            ],
        }

        with (
            mock.patch.object(websearch, "ai_search", return_value=invalid_result),
            mock.patch.object(websearch, "tavily_search", return_value=tavily_result) as tavily_search,
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef"),
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_search(args, cfg)

        tavily_search.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["sources_count"], 1)

    def test_search_limits_sources_and_cached_source_content(self) -> None:
        cfg = websearch.Config({"search_provider_priority": ["grok"]})
        args = self.search_args(max_sources=2, max_chars=20)
        result = {
            "answer": "answer",
            "sources": [
                {
                    "title": ("Title " * 20_000) if index == 0 else f"Source {index}",
                    "url": f"https://example.com/{index}",
                    "content": "x" * 100,
                    "published_date": "2026-07-10" * 1000,
                    "unexpected": "y" * 100_000,
                }
                for index in range(3)
            ],
        }

        with (
            mock.patch.object(websearch, "ai_search", return_value=result),
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef") as save_session,
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_search(args, cfg)

        session_payload = save_session.call_args.args[1]
        self.assertEqual(len(session_payload["sources"]), 2)
        self.assertLessEqual(
            len(session_payload["answer"])
            + sum(
                len(str(source.get(key, "")))
                for source in session_payload["sources"]
                for key in ("title", "content", "published_date")
            ),
            args.max_chars,
        )
        self.assertNotIn("unexpected", session_payload["sources"][0])
        response = json.loads(output.getvalue())
        self.assertEqual(response["sources_count"], 2)
        self.assertEqual(len(response["sources"]), 2)
        self.assertLess(len(output.getvalue()), 12_000)

    def test_search_limits_warning_size(self) -> None:
        secret = "super-secret-token"
        cfg = websearch.Config(
            {
                "search_provider_priority": ["grok", "tavily"],
                "grok_search_api_key": secret,
            }
        )
        args = self.search_args()
        fallback = {
            "answer": "answer",
            "sources": [{"title": "Example", "url": "https://example.com"}],
        }

        with (
            mock.patch.object(
                websearch,
                "ai_search",
                side_effect=websearch.HttpError(401, secret + ("x" * 100_000)),
            ),
            mock.patch.object(websearch, "tavily_search", return_value=fallback),
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef"),
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_search(args, cfg)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["warnings"])
        self.assertTrue(all(len(warning) <= 500 for warning in payload["warnings"]))
        self.assertNotIn(secret, " ".join(payload["warnings"]))

    def test_permanent_ai_error_skips_retries(self) -> None:
        cfg = websearch.Config(
            {
                "search_provider_priority": ["grok"],
                "grok_search_max_retries": 2,
            }
        )
        args = self.search_args()

        with (
            mock.patch.object(
                websearch,
                "ai_search",
                side_effect=websearch.HttpError(401, "request timed out"),
            ) as ai_search,
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef"),
            mock.patch.object(websearch.time, "sleep") as sleep,
            mock.patch("sys.stdout", io.StringIO()),
        ):
            websearch.command_search(args, cfg)

        ai_search.assert_called_once()
        sleep.assert_not_called()

    def test_transient_ai_error_retries_with_bounded_backoff(self) -> None:
        cfg = websearch.Config(
            {
                "search_provider_priority": ["grok"],
                "grok_search_max_retries": 3,
            }
        )
        args = self.search_args()
        result = {
            "answer": "answer",
            "sources": [{"title": "Example", "url": "https://example.com"}],
        }

        with (
            mock.patch.object(
                websearch,
                "ai_search",
                side_effect=[
                    websearch.HttpError(500, "temporary"),
                    websearch.HttpError(429, "rate limited"),
                    websearch.HttpError(408, "request timeout"),
                    result,
                ],
            ) as ai_search,
            mock.patch.object(websearch, "save_session", return_value="0123456789abcdef"),
            mock.patch.object(websearch.time, "sleep") as sleep,
            mock.patch("sys.stdout", io.StringIO()),
        ):
            websearch.command_search(args, cfg)

        self.assertEqual(ai_search.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_search_rejects_non_positive_max_sources(self) -> None:
        cfg = websearch.Config({"search_provider_priority": ["grok"]})

        for max_sources in (0, -1):
            with self.subTest(max_sources=max_sources):
                args = self.search_args(max_sources=max_sources)
                with (
                    mock.patch.object(websearch, "ai_search") as ai_search,
                    mock.patch("sys.stdout", io.StringIO()),
                    self.assertRaisesRegex(SystemExit, "max-sources must be between"),
                ):
                    websearch.command_search(args, cfg)
                ai_search.assert_not_called()

        args = self.search_args(max_sources=websearch.MAX_SEARCH_SOURCES + 1)
        with (
            mock.patch.object(websearch, "ai_search") as ai_search,
            mock.patch("sys.stdout", io.StringIO()),
            self.assertRaisesRegex(SystemExit, "max-sources must be between"),
        ):
            websearch.command_search(args, cfg)
        ai_search.assert_not_called()

    def test_commands_reject_negative_max_chars(self) -> None:
        cfg = websearch.Config({"search_provider_priority": ["grok"]})
        with (
            self.assertRaisesRegex(SystemExit, "max-chars must be non-negative"),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            websearch.command_search(self.search_args(max_chars=-1), cfg)

        with (
            self.assertRaisesRegex(SystemExit, "max-chars must be non-negative"),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            websearch.command_fetch(mock.Mock(url="https://example.com", max_chars=-1), cfg)

        with self.assertRaisesRegex(websearch.ConfigError, "GROK_SEARCH_RESPONSE_MAX_CHARS"):
            _ = websearch.Config({"grok_search_response_max_chars": -1}).response_max_chars
        with self.assertRaisesRegex(websearch.ConfigError, "GROK_SEARCH_FETCH_MAX_CHARS"):
            _ = websearch.Config({"grok_search_fetch_max_chars": -1}).fetch_max_chars

    def test_retry_count_has_a_hard_limit(self) -> None:
        cfg = websearch.Config({"grok_search_max_retries": 10_000})

        self.assertEqual(cfg.grok_search_max_retries, websearch.MAX_GROK_RETRIES)

    def test_plain_empty_content_continues_to_next_fetch_provider(self) -> None:
        cfg = websearch.Config({"fetch_provider_priority": ["plain", "tavily"]})
        args = mock.Mock(url="https://example.com", max_chars=100)

        with (
            mock.patch.object(websearch, "validate_web_url"),
            mock.patch.object(websearch, "plain_fetch", return_value=("", False)) as plain_fetch,
            mock.patch.object(websearch, "tavily_extract", return_value="tavily content") as tavily_extract,
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_fetch(args, cfg)

        plain_fetch.assert_called_once()
        self.assertEqual(plain_fetch.call_args.kwargs["max_chars"], args.max_chars)
        tavily_extract.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["content"], "tavily content")
        self.assertEqual(payload["source_type"], "tavily")

    def test_plain_error_continues_to_next_fetch_provider(self) -> None:
        secret = "fetch-secret-token"
        cfg = websearch.Config(
            {
                "fetch_provider_priority": ["plain", "tavily"],
                "tavily_api_key": secret,
            }
        )
        args = mock.Mock(url="https://example.com", max_chars=100)

        with (
            mock.patch.object(websearch, "validate_web_url"),
            mock.patch.object(websearch, "plain_fetch", side_effect=websearch.HttpError(503, secret + " temporary")),
            mock.patch.object(websearch, "tavily_extract", return_value="tavily content") as tavily_extract,
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_fetch(args, cfg)

        tavily_extract.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["source_type"], "tavily")
        self.assertIn("plain fetch failed", " ".join(payload["warnings"]))
        self.assertNotIn(secret, " ".join(payload["warnings"]))

    def test_plain_fetch_propagates_early_truncation(self) -> None:
        cfg = websearch.Config({"fetch_provider_priority": ["plain"]})
        args = mock.Mock(url="https://example.com", max_chars=10)

        with (
            mock.patch.object(websearch, "validate_web_url"),
            mock.patch.object(websearch, "plain_fetch", return_value=("x" * 10, True)),
            mock.patch("sys.stdout", io.StringIO()) as output,
        ):
            websearch.command_fetch(args, cfg)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["truncated"])
        self.assertGreater(payload["original_length"], len(payload["content"]))

    def test_duckduckgo_parser_limits_results_during_parsing(self) -> None:
        parser = websearch.DuckDuckGoHTMLParser(max_sources=2, max_events=1000)
        html = "".join(
            f'<a class="result__a" href="https://example.com/{index}">Title {index}</a>'
            for index in range(100)
        )

        with self.assertRaises(websearch._HTMLTextLimitReached):
            parser.feed(html)

        self.assertLessEqual(len(parser.results), 2)

    def test_main_redacts_top_level_http_errors(self) -> None:
        secret = "top-level-secret"
        args = mock.Mock(func=mock.Mock(side_effect=websearch.HttpError(503, secret + ("x" * 1000))))
        parser = mock.Mock()
        parser.parse_args.return_value = args

        with (
            mock.patch.object(websearch, "build_parser", return_value=parser),
            mock.patch.object(websearch, "load_file_config", return_value={"tavily_api_key": secret}),
            mock.patch("sys.stderr", io.StringIO()) as error,
        ):
            exit_code = websearch.main([])

        self.assertEqual(exit_code, 2)
        self.assertNotIn(secret, error.getvalue())
        self.assertLess(len(error.getvalue()), 600)

    def test_map_errors_are_redacted_and_bounded(self) -> None:
        secret = "map-secret-token"
        cfg = websearch.Config(
            {
                "map_provider_priority": ["tavily"],
                "tavily_api_key": secret,
            }
        )
        args = mock.Mock(url="https://example.com", max_results=5)

        with (
            mock.patch.object(websearch, "validate_web_url"),
            mock.patch.object(
                websearch,
                "tavily_map",
                side_effect=websearch.HttpError(503, secret + ("x" * 100_000)),
            ),
            self.assertRaises(SystemExit) as raised,
        ):
            websearch.command_map(args, cfg)

        message = str(raised.exception)
        self.assertNotIn(secret, message)
        self.assertLess(len(message), 600)
