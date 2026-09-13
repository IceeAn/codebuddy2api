"""固定资源下载与原子发布测试。"""

import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.download_tokenizers import download_catalog, verify_catalog


class TokenizerDownloadTests(unittest.TestCase):
    def test_verified_download_deduplicates_and_can_run_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assets"
            data = b"resource"
            item = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            entry = {
                "repo": "owner/model",
                "revision": "a" * 40,
                "files": {"tokenizer.json": item},
            }
            catalog = {"version": 1, "resources": [entry, entry]}
            calls = []

            def fetch(url):
                calls.append(url)
                return data

            download_catalog(catalog, root, fetch=fetch)
            self.assertEqual(len(calls), 1)
            verify_catalog(catalog, root)
            download_catalog(catalog, root, fetch=lambda _: self.fail("不得重复下载"))

    def test_failed_download_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assets"
            root.mkdir()
            (root / "old").write_bytes(b"old")
            catalog = {
                "version": 1,
                "resources": [
                    {
                        "repo": "owner/model",
                        "revision": "a" * 40,
                        "files": {"tokenizer.json": {"sha256": "b" * 64, "size": 1}},
                    }
                ],
            }
            with self.assertRaises(ValueError):
                download_catalog(catalog, root, fetch=lambda _: b"x")
            self.assertEqual((root / "old").read_bytes(), b"old")
