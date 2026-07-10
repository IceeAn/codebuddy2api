#!/usr/bin/env python3
"""组合 GitHub PR 说明与独立提交，生成统一的 Release 变更记录。"""

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
GENERATOR_COMMENT_PATTERN = re.compile(
    r"<!-- Release notes generated using configuration.*?-->",
    flags=re.DOTALL,
)
FULL_CHANGELOG_PATTERN = re.compile(r"\*\*Full Changelog\*\*:\s+\S.*")
WHATS_CHANGED_PATTERN = re.compile(r"## What's [Cc]hanged\s*")
SUPPORTED_HEADING_PATTERN = re.compile(r"#{2,3}[ \t]+(?P<title>\S.*)")
ATX_HEADING_PATTERN = re.compile(r"#{1,6}[ \t]+\S.*")


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
    command = ["git", "log", "--reverse", "--format=%H%x00%s", "-z", revision]
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


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _render_independent_commits(
    commits: list[Commit],
    repository_url: str,
) -> str:
    if not commits:
        return ""

    grouped = {"新功能": [], "Bug 修复": [], "其他变更": []}
    for commit in commits:
        grouped[_category(commit.subject)].append(commit)

    parts = ["### 独立提交"]
    repository_url = repository_url.rstrip("/")
    for title, category_commits in grouped.items():
        if not category_commits:
            continue
        lines = [f"#### {title}", ""]
        for commit in category_commits:
            commit_url = f"{repository_url}/commit/{commit.sha}"
            lines.append(f"- [`{commit.sha[:7]}`]({commit_url}) {commit.subject}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _render_generated_pull_requests(lines: list[str]) -> str:
    parts: list[str] = []
    prefix_lines: list[str] = []
    current_title: str | None = None
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_title, current_lines
        content_lines = _trim_blank_lines(current_lines)
        if current_title is None:
            if content_lines:
                parts.append("\n".join(content_lines))
        elif content_lines:
            parts.append(f"#### {current_title}\n\n" + "\n".join(content_lines))
        current_title = None
        current_lines = []

    for line in lines:
        if WHATS_CHANGED_PATTERN.fullmatch(line):
            continue

        heading = SUPPORTED_HEADING_PATTERN.fullmatch(line)
        if heading:
            if current_title is None:
                prefix_lines.extend(current_lines)
                current_lines = prefix_lines
            flush_current()
            prefix_lines = []
            current_title = heading.group("title")
            continue

        if ATX_HEADING_PATTERN.fullmatch(line):
            raise RuntimeError("GitHub 生成说明包含不支持的标题层级")
        current_lines.append(line)

    if current_title is None:
        prefix_lines.extend(current_lines)
        current_lines = prefix_lines
    flush_current()
    return "\n\n".join(parts)


def _parse_generated_notes(generated_notes: str) -> tuple[str, str, str]:
    comment_matches = list(GENERATOR_COMMENT_PATTERN.finditer(generated_notes))
    if len(comment_matches) > 1:
        raise RuntimeError("GitHub 生成说明包含多个来源注释")

    generator_comment = ""
    if comment_matches:
        match = comment_matches[0]
        generator_comment = match.group(0).strip()
        generated_notes = generated_notes[: match.start()] + generated_notes[match.end() :]

    full_changelog_lines = []
    content_lines = []
    for line in generated_notes.splitlines():
        stripped = line.strip()
        if FULL_CHANGELOG_PATTERN.fullmatch(stripped):
            full_changelog_lines.append(stripped)
        else:
            content_lines.append(line)

    if len(full_changelog_lines) != 1:
        raise RuntimeError("GitHub 生成说明必须包含且仅包含一个 Full Changelog")

    pull_request_notes = _render_generated_pull_requests(
        _trim_blank_lines(content_lines)
    )
    return generator_comment, pull_request_notes, full_changelog_lines[0]


def build_release_change_notes(
    repository: str,
    repository_url: str,
    tag: str,
    previous_tag: str | None,
    generated_notes: str,
    run: RunCommand = subprocess.run,
) -> str:
    commits = _load_commits(tag, previous_tag, run)
    independent_commits = [
        commit
        for commit in commits
        if not DIRECT_RELEASE_PATTERN.match(commit.subject)
        and not _has_associated_pull_request(repository, commit, run)
    ]
    independent_notes = _render_independent_commits(
        independent_commits,
        repository_url,
    )
    generator_comment, pull_request_notes, full_changelog = _parse_generated_notes(
        generated_notes
    )

    parts = ["## 变更记录"]
    if generator_comment:
        parts.append(generator_comment)
    if independent_notes:
        parts.append(independent_notes)
    if pull_request_notes:
        parts.append(f"### 合并的 PR\n\n{pull_request_notes}")
    parts.append(full_changelog)
    return "\n\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--repository-url", required=True, help="GitHub repository URL")
    parser.add_argument("--tag", required=True, help="Current release tag")
    parser.add_argument("--previous-tag", help="Previous stable release tag")
    parser.add_argument(
        "--generated-notes",
        required=True,
        type=Path,
        help="GitHub generated release notes path",
    )
    parser.add_argument("--output", required=True, type=Path, help="Markdown output path")
    args = parser.parse_args()

    notes = build_release_change_notes(
        repository=args.repository,
        repository_url=args.repository_url,
        tag=args.tag,
        previous_tag=args.previous_tag,
        generated_notes=args.generated_notes.read_text(encoding="utf-8"),
    )
    args.output.write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
