"""用户映射优先级、文件隔离与资源更新测试。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import hashlib
import json
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from unittest import mock

import config
from tests.helpers import TempConfigMixin
from tests.test_tokenizer import tokenizer_files
from src.tokenizer_store import TokenizerStore, read_verified
from src.tokenizer_engine import TokenizerError


class TokenizerStoreTests(TempConfigMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config.initialize_database()
        self.catalog = {"version": 1, "resources": [], "pending_models": ["pending"]}
        self.store = TokenizerStore(
            config.get_database_path(),
            self.temp_path / "resources",
            self.catalog,
            self.temp_path / "builtin",
        )

    def test_slow_read_does_not_block_other_resources_or_database_writes(self):
        files = tokenizer_files()
        for username in ("alice", "bob"):
            resource = self.store.create(username, "词表", files, {})
            self.store.save_mappings(username, {"model": resource["id"]})
        other = TokenizerStore(
            config.get_database_path(), self.store.root, self.catalog,
        )
        entered, release = threading.Event(), threading.Event()
        alice_directory = self.store._user_dir("alice")

        def slow_read(path, metadata):
            if path.parent.parent == alice_directory:
                entered.set()
                self.assertTrue(release.wait(10))
            return read_verified(path, metadata)

        def unrelated_work():
            snapshot = other.resolve("bob", "model")
            other.save_mappings("bob", {})
            return snapshot

        with ThreadPoolExecutor(max_workers=2) as pool, mock.patch(
            "src.tokenizer_store.read_verified", side_effect=slow_read,
        ):
            pending = pool.submit(self.store.resolve, "alice", "model")
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(pool.submit(unrelated_work).result(timeout=2)["files"], files)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2)["files"], files)

    def test_delete_waits_for_snapshot_across_store_instances_without_holding_database_lock(self):
        files = tokenizer_files()
        resource = self.store.create("alice", "词表", files, {})
        self.store.save_mappings("alice", {"model": resource["id"]})
        other = TokenizerStore(config.get_database_path(), self.store.root, self.catalog)
        entered, release, deleting = threading.Event(), threading.Event(), threading.Event()

        def slow_read(path, metadata):
            entered.set()
            self.assertTrue(release.wait(10))
            return read_verified(path, metadata)

        def delete():
            deleting.set()
            other.delete("alice", resource["id"])

        with ThreadPoolExecutor(max_workers=3) as pool, mock.patch(
            "src.tokenizer_store.read_verified", side_effect=slow_read,
        ):
            pending = pool.submit(self.store.resolve, "alice", "model")
            try:
                self.assertTrue(entered.wait(2))
                pool.submit(other.save_mappings, "alice", {}).result(timeout=2)
                removed = pool.submit(delete)
                self.assertTrue(deleting.wait(2))
                with self.assertRaises(FutureTimeoutError):
                    removed.result(timeout=0.1)
                pool.submit(other.save_mappings, "bob", {}).result(timeout=2)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2)["files"], files)
            removed.result(timeout=2)
        self.assertEqual(other.listing("alice")["user_resources"], [])
        self.assertFalse((other._user_dir("alice") / resource["id"]).exists())

    def test_resource_deleted_after_mapping_lookup_returns_not_found(self):
        resource = self.store.create("alice", "词表", tokenizer_files(), {})
        self.store.save_mappings("alice", {"model": resource["id"]})
        other = TokenizerStore(config.get_database_path(), self.store.root, self.catalog)
        selected, release = threading.Event(), threading.Event()
        resource_lock = self.store._resource_lock

        def delayed_lock(username, resource_id):
            selected.set()
            self.assertTrue(release.wait(10))
            return resource_lock(username, resource_id)

        with ThreadPoolExecutor(max_workers=1) as pool, mock.patch.object(
            self.store, "_resource_lock", side_effect=delayed_lock,
        ), mock.patch("src.tokenizer_store.read_verified") as read:
            pending = pool.submit(self.store.resolve, "alice", "model")
            try:
                self.assertTrue(selected.wait(2))
                other.save_mappings("alice", {})
                other.delete("alice", resource["id"])
            finally:
                release.set()
            with self.assertRaises(TokenizerError) as caught:
                pending.result(timeout=2)
            self.assertEqual(caught.exception.status_code, 404)
            read.assert_not_called()

    def test_failed_read_releases_resource_for_delete(self):
        resource = self.store.create("alice", "词表", tokenizer_files(), {})
        self.store.save_mappings("alice", {"model": resource["id"]})
        with mock.patch("src.tokenizer_store.read_verified", side_effect=RuntimeError("校验失败")):
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                self.store.resolve("alice", "model")
        self.store.save_mappings("alice", {})
        other = TokenizerStore(config.get_database_path(), self.store.root, self.catalog)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(other.delete, "alice", resource["id"]).result(timeout=2)
        self.assertEqual(other.listing("alice")["user_resources"], [])

    def test_upload_map_reload_and_user_isolation(self):
        resource = self.store.create(
            "alice",
            "自定义词表",
            tokenizer_files(),
            {"encoder": "auto", "method": "budget_v1", "format": "hf"},
        )
        self.store.save_mappings("alice", {"my-model": resource["id"]})
        snapshot = self.store.resolve("alice", "my-model")
        self.assertEqual(snapshot["files"], tokenizer_files())
        self.assertEqual(snapshot["source"], "user")
        self.assertEqual(self.store.listing("bob")["user_resources"], [])
        with self.assertRaises(TokenizerError):
            self.store.resolve("bob", "my-model")
        with self.assertRaises(TokenizerError):
            self.store.save_mappings("bob", {"my-model": resource["id"]})

    def test_referenced_resource_cannot_be_deleted(self):
        resource = self.store.create(
            "alice", "词表", tokenizer_files(), {"encoder": "auto"}
        )
        self.store.save_mappings("alice", {"model": resource["id"]})
        with self.assertRaises(TokenizerError) as caught:
            self.store.delete("alice", resource["id"])
        self.assertEqual(caught.exception.status_code, 409)
        self.store.save_mappings("alice", {})
        self.store.delete("alice", resource["id"])
        self.assertEqual(self.store.listing("alice")["user_resources"], [])

    def test_builtin_snapshot_survives_package_update(self):
        files = tokenizer_files()
        blobs = self.temp_path / "builtin"
        blobs.mkdir()
        manifest = {}
        for name, data in files.items():
            digest = hashlib.sha256(data).hexdigest()
            (blobs / digest).write_bytes(data)
            manifest[name] = {"sha256": digest, "size": len(data)}
        entry = {
            "id": "base",
            "model": "model",
            "revision": "old",
            "repo": "example/base",
            "files": manifest,
            "encoder": "auto",
            "format": "hf",
        }
        self.catalog["resources"].append(entry)
        self.assertEqual(self.store.resolve("alice", "model")["source"], "builtin")
        resource = self.store.snapshot_builtin("alice", "base")
        self.store.save_mappings("alice", {"model": resource["id"]})
        self.assertFalse(
            self.store.listing("alice")["user_resources"][0]["update_available"]
        )
        updated_data = files["tokenizer.json"] + b" "
        digest = hashlib.sha256(updated_data).hexdigest()
        (blobs / digest).write_bytes(updated_data)
        entry["files"] = {
            "tokenizer.json": {"sha256": digest, "size": len(updated_data)}
        }
        entry["revision"] = "new"
        self.assertTrue(
            self.store.listing("alice")["user_resources"][0]["update_available"]
        )
        self.assertEqual(self.store.resolve("alice", "model")["files"], files)
        self.store.save_mappings("alice", {})
        self.assertEqual(
            self.store.resolve("alice", "model")["files"]["tokenizer.json"],
            updated_data,
        )
        self.store.save_mappings("alice", {"model": resource["id"]})
        self.catalog["resources"].clear()
        self.assertEqual(self.store.resolve("alice", "model")["files"], files)
        self.assertEqual(
            self.store.listing("alice")["mappings"], {"model": resource["id"]}
        )

    def test_files_and_directories_are_verified(self):
        from unittest import mock
        from src.tokenizer_store import (
            safe_directory,
            read_verified,
            read_catalog,
            verify_builtin,
        )

        file = self.temp_path / "file"
        file.write_bytes(b"x")
        link = self.temp_path / "link"
        link.symlink_to(file)
        with self.assertRaises(RuntimeError):
            safe_directory(link)
        with mock.patch("pathlib.Path.mkdir"), self.assertRaises(RuntimeError):
            safe_directory(file)
        item = {"size": 2, "sha256": hashlib.sha256(b"x").hexdigest()}
        with self.assertRaises(RuntimeError):
            read_verified(file, item)
        with self.assertRaises(RuntimeError):
            read_verified(file, {"size": 1, "sha256": "bad"})
        with mock.patch("src.tokenizer_store.CATALOG_PATH", file):
            file.write_text('{"version":2}')
            with self.assertRaises(RuntimeError):
                read_catalog()
        with mock.patch("src.tokenizer_store.get_tokenizer_store", side_effect=OSError):
            with self.assertRaisesRegex(RuntimeError, "download_tokenizers"):
                verify_builtin()
        resource = self.store.create("alice", "词表", tokenizer_files(), {})
        self.store.save_mappings("alice", {"model": resource["id"]})
        directory = self.store._user_dir("alice") / resource["id"]
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.store.resolve("alice", "model")

    def test_invalid_names_mappings_and_failed_upload_cleanup(self):
        from unittest import mock

        for name in ("", 42, "x" * 81):
            with self.assertRaises(TokenizerError):
                self.store.create("alice", name, tokenizer_files(), {})
        for files in ({"../escape": b"x"}, {".hidden": b"x"}):
            with self.assertRaises(TokenizerError):
                self.store.create("alice", "词表", files, {})
        owner = self.store._user_dir("alice")
        self.assertEqual(list(owner.iterdir()), [])
        with mock.patch.object(self.store.database, "connect", side_effect=OSError):
            with self.assertRaises(OSError):
                self.store.create("alice", "词表", tokenizer_files(), {})
        self.assertEqual(list(owner.iterdir()), [])
        for mappings in ({"bad model": "x"}, {"model": 42}, []):
            with self.assertRaises(TokenizerError):
                self.store.save_mappings("alice", mappings)

    def test_failed_database_delete_restores_directory_and_cleanup_failure_is_committed(
        self,
    ):
        from unittest import mock
        from contextlib import contextmanager

        resource = self.store.create("alice", "词表", tokenizer_files(), {})
        connect = self.store.database.connect

        @contextmanager
        def broken():
            with connect() as connection:
                proxy = mock.Mock(wraps=connection)
                proxy.commit.side_effect = OSError("提交失败")
                yield proxy

        with mock.patch.object(self.store.database, "connect", broken):
            with self.assertRaises(OSError):
                self.store.delete("alice", resource["id"])
        directory = self.store._user_dir("alice") / resource["id"]
        self.assertTrue(directory.is_dir())
        self.assertEqual(len(self.store.listing("alice")["user_resources"]), 1)
        with mock.patch(
            "src.tokenizer_store.shutil.rmtree", side_effect=OSError
        ), self.assertLogs("src.tokenizer_store", level="WARNING"):
            self.store.delete("alice", resource["id"])
        self.assertEqual(self.store.listing("alice")["user_resources"], [])

    def test_version_five_migration_adds_resources_without_rebuilding_existing_tables(
        self,
    ):
        with self.store.database.connect() as connection:
            connection.execute("DROP TABLE tokenizer_model_mappings")
            connection.execute("DROP TABLE tokenizer_resources")
            connection.execute("CREATE TABLE migration_marker(value TEXT)")
            connection.execute("INSERT INTO migration_marker VALUES ('preserve')")
            connection.execute("PRAGMA user_version=5")
        with self.store.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(
                connection.execute("SELECT value FROM migration_marker").fetchone()[0],
                "preserve",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM tokenizer_resources"
                ).fetchone()[0],
                0,
            )

    def test_symlinks_are_rejected_even_without_platform_nofollow_flag(self):
        from unittest import mock
        import os
        from src.tokenizer_store import read_verified

        data = b"hello"
        item = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        target = self.temp_path / "target"
        target.write_bytes(data)
        link = self.temp_path / "alias"
        link.symlink_to(target)
        with mock.patch.object(os, "O_NOFOLLOW", 0):
            self.assertEqual(read_verified(target, item), data)
            with self.assertRaises(RuntimeError):
                read_verified(link, item)
