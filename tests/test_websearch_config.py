from __future__ import annotations

import os
import sys
import tempfile
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
