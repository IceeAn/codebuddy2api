#!/usr/bin/env python3
"""按仓库清单下载内置分词数据；不下载权重，不执行远程代码。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _items(catalog):
    if catalog["version"] != 1:
        raise ValueError("不支持的资源清单版本")
    seen = set()
    for resource in catalog["resources"]:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", resource["repo"]) or not re.fullmatch(
            r"[a-f0-9]{40}", resource["revision"]
        ):
            raise ValueError("资源来源必须是固定的官方仓库 commit")
        for name, item in resource["files"].items():
            digest = item["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", digest) or Path(name).name != name:
                raise ValueError("资源清单包含非法路径或摘要")
            if digest not in seen:
                seen.add(digest)
                yield digest, item, f"https://huggingface.co/{resource['repo']}/resolve/{resource['revision']}/{name}"


def _check(data, item):
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("Tokenizer 下载内容与锁定清单不符")


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Tokenizer 文件不能是符号链接")
    with os.fdopen(
        os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        ),
        "rb",
    ) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Tokenizer 资源必须是普通文件")
        return stream.read()


def _fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read(64 * 1024 * 1024 + 1)
        except OSError:
            if attempt == 2:
                raise


def verify_catalog(catalog, destination):
    destination = Path(destination)
    if destination.is_symlink() or (destination / "blobs").is_symlink():
        raise ValueError("资源目录不能是符号链接")
    for digest, item, _url in _items(catalog):
        _check(_read(destination / "blobs" / digest), item)


def download_catalog(catalog, destination, *, fetch=_fetch, cache_dir=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("资源目录不能是符号链接")
    with tempfile.TemporaryDirectory(
        prefix=".tokenizer-download-", dir=destination.parent
    ) as temporary:
        stage = Path(temporary) / "assets"
        (stage / "blobs").mkdir(parents=True)
        for digest, item, url in _items(catalog):
            cached = (
                Path(cache_dir) / digest
                if cache_dir
                else destination / "blobs" / digest
            )
            data = _read(cached) if cached.exists() else fetch(url)
            _check(data, item)
            (stage / "blobs" / digest).write_bytes(data)
        verify_catalog(catalog, stage)
        backup = Path(temporary) / "previous"
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(stage, destination)
        except BaseException:
            if backup.exists():
                os.replace(backup, destination)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="只验证已下载资源")
    parser.add_argument("--cache-dir", type=Path, help="读取已校验文件的本地缓存目录")
    args = parser.parse_args(argv)
    catalog = json.loads(
        (PROJECT_ROOT / "src/tokenizer_catalog.json").read_text(encoding="utf-8")
    )
    destination = PROJECT_ROOT / "src/tokenizer_assets"
    if args.verify_only:
        verify_catalog(catalog, destination)
    else:
        download_catalog(catalog, destination, cache_dir=args.cache_dir)
    print("内置 Tokenizer 资源校验完成")


if __name__ == "__main__":
    main()
