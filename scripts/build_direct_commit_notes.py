#!/usr/bin/env python3
"""为没有关联 PR 的直接提交生成分类 Release 说明。"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
FEATURE_PATTERN = re.compile(r"^feat(?:\([^\r\n)]+\))?!?:")
FIX_PATTERN = re.compile(r"^fix(?:\([^\r\n)]+\))?!?:")
DIRECT_RELEASE_PATTERN = re.compile(r"^chore\(release\)!?:")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str


RunCommand = Callable[..., subprocess.CompletedProcess]


def _load_commits(
    tag: str,
    previous_tag: str | None,
    run: RunCommand,
) -> list[Commit]:
    revision = f"{previous_tag}..{tag}" if previous_tag else tag
    command = ["git", "log", "--format=%H%x00%s", "-z", revision]
    result = run(command, check=True, capture_output=True)
    output = result.stdout
    if not isinstance(output, bytes):
        raise RuntimeError("git log 必须返回字节输出")

    fields = output.decode("utf-8").split("\0")
    if fields[-1:] == [""]:
        fields.pop()
    if len(fields) % 2 != 0:
        raise RuntimeError("无法解析 git log 输出")

    commits = []
    for index in range(0, len(fields), 2):
        sha, subject = fields[index : index + 2]
        if not SHA_PATTERN.fullmatch(sha) or not subject:
            raise RuntimeError("git log 返回了无效提交")
        commits.append(Commit(sha=sha, subject=subject))
    return commits


def _has_associated_pull_request(
    repository: str,
    commit: Commit,
    run: RunCommand,
) -> bool:
    endpoint = f"repos/{repository}/commits/{commit.sha}/pulls"
    command = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        endpoint,
        "--jq",
        "length",
    ]
    result = run(command, check=True, capture_output=True, text=True)
    try:
        count = int(result.stdout.strip())
    except (AttributeError, ValueError) as error:
        raise RuntimeError("GitHub API 返回了无效的关联 PR 数量") from error
    if count < 0:
        raise RuntimeError("GitHub API 返回了无效的关联 PR 数量")
    return count > 0


def _category(subject: str) -> str:
    if FEATURE_PATTERN.match(subject):
        return "新功能"
    if FIX_PATTERN.match(subject):
        return "Bug 修复"
    return "其他变更"


def _render_notes(commits: list[Commit], repository_url: str) -> str:
    if not commits:
        return ""

    grouped = {"新功能": [], "Bug 修复": [], "其他变更": []}
    for commit in commits:
        grouped[_category(commit.subject)].append(commit)

    lines = ["## 直接提交", ""]
    repository_url = repository_url.rstrip("/")
    for title, category_commits in grouped.items():
        if not category_commits:
            continue
        lines.extend((f"### {title}", ""))
        for commit in category_commits:
            commit_url = f"{repository_url}/commit/{commit.sha}"
            lines.append(f"- [`{commit.sha[:7]}`]({commit_url}) {commit.subject}")
        lines.append("")
    return "\n".join(lines)


def build_direct_commit_notes(
    repository: str,
    repository_url: str,
    tag: str,
    previous_tag: str | None,
    run: RunCommand = subprocess.run,
) -> str:
    commits = _load_commits(tag, previous_tag, run)
    direct_commits = [
        commit
        for commit in commits
        if not _has_associated_pull_request(repository, commit, run)
        and not DIRECT_RELEASE_PATTERN.match(commit.subject)
    ]
    return _render_notes(direct_commits, repository_url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--repository-url", required=True, help="GitHub repository URL")
    parser.add_argument("--tag", required=True, help="Current release tag")
    parser.add_argument("--previous-tag", help="Previous stable release tag")
    parser.add_argument("--output", required=True, type=Path, help="Markdown output path")
    args = parser.parse_args()

    notes = build_direct_commit_notes(
        repository=args.repository,
        repository_url=args.repository_url,
        tag=args.tag,
        previous_tag=args.previous_tag,
    )
    args.output.write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
