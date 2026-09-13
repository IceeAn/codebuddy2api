"""内置只读资源与用户私有资源的独立存储。"""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from weakref import WeakValueDictionary

from .sqlite_database import SQLiteDatabase
from .tokenizer_engine import TokenizerError, canonical_json

CATALOG_PATH = Path(__file__).with_name("tokenizer_catalog.json")
BUILTIN_BLOBS = Path(__file__).with_name("tokenizer_assets") / "blobs"
logger = logging.getLogger(__name__)
_resource_locks = WeakValueDictionary()
_resource_locks_guard = threading.Lock()


def safe_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Tokenizer 目录不能是符号链接")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError("Tokenizer 资源路径不是目录")
    return path


def read_verified(path, metadata):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Tokenizer 文件不能是符号链接")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != metadata["size"]:
            raise RuntimeError("Tokenizer 文件类型或大小错误")
        data = stream.read()
    if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise RuntimeError("Tokenizer 文件校验失败")
    return data


def read_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog["version"] != 1:
        raise RuntimeError("不支持的 Tokenizer 清单版本")
    return catalog


def verify_builtin():
    """启动校验不解析分词器；缺失数据时明确失败。"""
    try:
        store = get_tokenizer_store()
        seen = set()
        for entry in store.catalog["resources"]:
            for metadata in entry["files"].values():
                digest = metadata["sha256"]
                if digest not in seen:
                    read_verified(store.builtin_blobs / digest, metadata)
                    seen.add(digest)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise RuntimeError(
            "内置 Tokenizer 资源缺失或损坏，请执行 python3 scripts/download_tokenizers.py"
        ) from error


class TokenizerStore:
    def __init__(self, database_path, user_root, catalog, builtin_blobs=BUILTIN_BLOBS):
        self.database = SQLiteDatabase(database_path)
        self.root = Path(user_root)
        self.catalog = catalog
        self.builtin_blobs = Path(builtin_blobs)

    def _resource_lock(self, username, resource_id):
        # 各请求创建独立 Store；锁须跨实例共享，最后一个使用者退出后自动回收。
        key = (self.database.path.resolve(), username, resource_id)
        with _resource_locks_guard:
            return _resource_locks.setdefault(key, threading.Lock())

    def _user_dir(self, username):
        safe_directory(self.root.parent)
        root = safe_directory(self.root)
        return safe_directory(root / hashlib.sha256(username.encode()).hexdigest())

    def _resource(self, connection, username, resource_id):
        row = connection.execute(
            "SELECT * FROM tokenizer_resources WHERE username=? AND id=?",
            (username, resource_id),
        ).fetchone()
        if row is None:
            raise TokenizerError("未找到用户分词资源", 404)
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            **json.loads(row["metadata_json"]),
        }

    def listing(self, username):
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            resources = [
                self._resource(connection, username, row["id"])
                for row in connection.execute(
                    "SELECT id FROM tokenizer_resources WHERE username=? ORDER BY created_at, id",
                    (username,),
                ).fetchall()
            ]
            mappings = {
                row["model"]: row["resource_id"]
                for row in connection.execute(
                    "SELECT model, resource_id FROM tokenizer_model_mappings WHERE username=? ORDER BY model",
                    (username,),
                )
            }
        builtin = {entry["id"]: entry for entry in self.catalog["resources"]}
        for resource in resources:
            origin = resource.get("origin")
            current = builtin.get(origin["id"]) if origin else None
            resource["update_available"] = bool(
                current
                and resource["revision"]
                != self._revision(current["files"], self._profile(current))
            )
        return {
            "builtin_resources": self.catalog["resources"],
            "pending_models": self.catalog["pending_models"],
            "user_resources": resources,
            "mappings": mappings,
        }

    @staticmethod
    def _profile(entry):
        return {"encoder": entry["encoder"], "format": entry["format"]}

    @staticmethod
    def _revision(files, profile):
        return hashlib.sha256(
            canonical_json({"files": files, "profile": profile}).encode()
        ).hexdigest()

    def _builtin(self, resource_id):
        for entry in self.catalog["resources"]:
            if entry["id"] == resource_id:
                return entry
        raise TokenizerError("未找到内置分词资源", 404)

    def _read_builtin(self, entry):
        return {
            name: read_verified(self.builtin_blobs / item["sha256"], item)
            for name, item in entry["files"].items()
        }

    def create(self, username, name, files, profile, origin=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise TokenizerError("资源名称必须为 1 至 80 个字符")
        owner = self._user_dir(username)
        resource_id = uuid.uuid4().hex
        metadata = {
            filename: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for filename, data in files.items()
        }
        record = {
            "files": metadata,
            "profile": profile,
            "origin": origin,
            "revision": self._revision(metadata, profile),
        }
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=owner))
        destination = owner / resource_id
        try:
            for filename, data in files.items():
                if Path(filename).name != filename or filename.startswith("."):
                    raise TokenizerError("无效的资源文件名")
                fd = os.open(
                    temporary / filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(temporary, destination)
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO tokenizer_resources VALUES (?, ?, ?, ?, ?)",
                    (
                        username,
                        resource_id,
                        name.strip(),
                        canonical_json(record),
                        int(time.time()),
                    ),
                )
        except BaseException:
            shutil.rmtree(destination if destination.exists() else temporary)
            raise
        return {
            "id": resource_id,
            "name": name.strip(),
            "update_available": False,
            **record,
        }

    def snapshot_builtin(self, username, resource_id):
        entry = self._builtin(resource_id)
        return self.create(
            username,
            entry["id"],
            self._read_builtin(entry),
            self._profile(entry),
            {"id": entry["id"], "repo": entry["repo"], "revision": entry["revision"]},
        )

    def save_mappings(self, username, mappings):
        from config import get_tokenizer_limits

        if (
            not isinstance(mappings, dict)
            or len(mappings) > get_tokenizer_limits()["max_mappings"]
        ):
            raise TokenizerError("模型映射必须是对象，且不超过配置的数量上限")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for model, resource_id in mappings.items():
                if not isinstance(model, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model
                ):
                    raise TokenizerError("模型 ID 格式无效")
                if not isinstance(resource_id, str):
                    raise TokenizerError("资源 ID 必须是字符串")
                self._resource(connection, username, resource_id)
            connection.execute(
                "DELETE FROM tokenizer_model_mappings WHERE username=?", (username,)
            )
            connection.executemany(
                "INSERT INTO tokenizer_model_mappings VALUES (?, ?, ?)",
                [
                    (username, model, resource_id)
                    for model, resource_id in mappings.items()
                ],
            )

    def resolve(self, username, model):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT resource_id FROM tokenizer_model_mappings WHERE username=? AND model=?",
                (username, model),
            ).fetchone()
        if row is not None:
            # 映射选择后允许在途请求使用该资源；加锁后重新确认资源尚未删除。
            with self._resource_lock(username, row["resource_id"]):
                with self.database.connect() as connection:
                    entry = self._resource(connection, username, row["resource_id"])
                # 文件 I/O 不持有数据库事务，读取期间由资源锁阻止删除。
                directory = self._user_dir(username) / entry["id"]
                if directory.is_symlink():
                    raise RuntimeError("Tokenizer 资源目录不能是符号链接")
                files = {
                    name: read_verified(directory / name, item)
                    for name, item in entry["files"].items()
                }
                return {
                    "files": files,
                    "profile": entry["profile"],
                    "source": "user",
                    "revision": entry["revision"],
                }
        for entry in self.catalog["resources"]:
            if entry["model"] == model:
                profile = self._profile(entry)
                return {
                    "files": self._read_builtin(entry),
                    "profile": profile,
                    "source": "builtin",
                    "revision": self._revision(entry["files"], profile),
                }
        raise TokenizerError("模型尚未配置匹配的 Tokenizer，请在设置页添加映射", 404)

    def delete(self, username, resource_id):
        # 必须先取得资源锁，再申请数据库写锁，避免阻塞其他资源及用户的写入。
        with self._resource_lock(username, resource_id), self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._resource(connection, username, resource_id)
            if connection.execute(
                "SELECT 1 FROM tokenizer_model_mappings WHERE username=? AND resource_id=? LIMIT 1",
                (username, resource_id),
            ).fetchone():
                raise TokenizerError("资源仍被模型映射引用，请先移除映射", 409)
            directory = self._user_dir(username) / resource_id
            tombstone = directory.with_name(".delete-" + uuid.uuid4().hex)
            os.replace(directory, tombstone)
            try:
                connection.execute(
                    "DELETE FROM tokenizer_resources WHERE username=? AND id=?",
                    (username, resource_id),
                )
                connection.commit()
            except BaseException:
                os.replace(tombstone, directory)
                raise
        try:
            shutil.rmtree(tombstone)
        except OSError:
            logger.warning("Tokenizer 已删除，但残留文件清理失败")


def get_tokenizer_store():
    import config

    return TokenizerStore(
        config.get_database_path(),
        Path(config.get_data_dir()) / "tokenizers",
        read_catalog(),
    )
