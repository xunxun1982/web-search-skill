from __future__ import annotations

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
