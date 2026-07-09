import subprocess
import unittest
from unittest import mock

from scripts.build_direct_commit_notes import build_direct_commit_notes


class DirectCommitNotesTests(unittest.TestCase):
    def _completed(self, command, stdout):
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    def test_excludes_pr_commits_and_groups_direct_commits(self):
        commits = (
            "a" * 40,
            "feat(api): 新增接口",
            "b" * 40,
            "fix!: 修复认证绕过",
            "c" * 40,
            "docs: 更新文档",
            "d" * 40,
            "feat: 已通过 PR 合并",
        )
        git_output = "\0".join((*commits, "")).encode()
        api_results = iter(("0\n", "0\n", "0\n", "1\n"))

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, git_output)
            return self._completed(command, next(api_results))

        notes = build_direct_commit_notes(
            repository="IceeAn/codebuddy2api",
            repository_url="https://github.com/IceeAn/codebuddy2api",
            tag="v0.0.2",
            previous_tag="v0.0.1",
            run=run,
        )

        self.assertIn("## 直接提交", notes)
        self.assertIn("### 新功能", notes)
        self.assertIn("feat(api): 新增接口", notes)
        self.assertIn("### Bug 修复", notes)
        self.assertIn("fix!: 修复认证绕过", notes)
        self.assertIn("### 其他变更", notes)
        self.assertIn("docs: 更新文档", notes)
        self.assertNotIn("已通过 PR 合并", notes)

    def test_uses_requested_comparison_range_and_queries_each_commit(self):
        sha = "a" * 40
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "git":
                return self._completed(command, f"{sha}\0chore: 调整配置\0".encode())
            return self._completed(command, "0\n")

        build_direct_commit_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v1.1.0",
            previous_tag="v1.0.0",
            run=run,
        )

        self.assertEqual(calls[0][0][-1], "v1.0.0..v1.1.0")
        self.assertIn(f"repos/owner/repo/commits/{sha}/pulls", calls[1][0])
        self.assertIn("--jq", calls[1][0])
        self.assertIn("length", calls[1][0])

    def test_first_release_includes_all_commits_reachable_from_tag(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return self._completed(command, b"")

        notes = build_direct_commit_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v0.0.1",
            previous_tag=None,
            run=run,
        )

        self.assertEqual(notes, "")
        self.assertEqual(calls[0][-1], "v0.0.1")

    def test_excludes_only_direct_release_commits(self):
        commits = (
            "a" * 40,
            "chore(release): 发布 v1.0.0",
            "b" * 40,
            "chore(release)!: 发布 v2.0.0",
            "c" * 40,
            "chore(release-notes): 调整发布说明",
            "d" * 40,
            "chore: 更新版本号",
        )
        git_output = "\0".join((*commits, "")).encode()

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, git_output)
            return self._completed(command, "0\n")

        notes = build_direct_commit_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v2.0.0",
            previous_tag="v1.0.0",
            run=run,
        )

        self.assertNotIn("发布 v1.0.0", notes)
        self.assertNotIn("发布 v2.0.0", notes)
        self.assertIn("chore(release-notes): 调整发布说明", notes)
        self.assertIn("chore: 更新版本号", notes)

    def test_rejects_invalid_api_count(self):
        sha = "a" * 40

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, f"{sha}\0fix: 修复问题\0".encode())
            return self._completed(command, "not-a-count\n")

        with self.assertRaisesRegex(RuntimeError, "GitHub API"):
            build_direct_commit_notes(
                repository="owner/repo",
                repository_url="https://example.com/owner/repo",
                tag="v1.0.0",
                previous_tag=None,
                run=run,
            )

    def test_subprocess_failures_are_not_hidden(self):
        with self.assertRaises(subprocess.CalledProcessError):
            build_direct_commit_notes(
                repository="owner/repo",
                repository_url="https://example.com/owner/repo",
                tag="v1.0.0",
                previous_tag=None,
                run=mock.Mock(
                    side_effect=subprocess.CalledProcessError(1, ["git", "log"])
                ),
            )


if __name__ == "__main__":
    unittest.main()
