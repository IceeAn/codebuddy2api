import subprocess
import unittest
from unittest import mock

from scripts.build_release_change_notes import build_release_change_notes


GENERATED_WITHOUT_PULL_REQUESTS = """\
<!-- Release notes generated using configuration in .github/release.yml at v0.1.0 -->

**Full Changelog**: https://github.com/IceeAn/codebuddy2api/compare/v0.0.1...v0.1.0
"""


class ReleaseChangeNotesTests(unittest.TestCase):
    def _completed(self, command, stdout):
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    def test_renders_independent_commits_in_oldest_first_order_per_category(self):
        commits = (
            "1" * 40,
            "style: 格式化前端文件",
            "2" * 40,
            "ci: 新增 GitHub Actions",
            "3" * 40,
            "feat: 用户文件不存在时让启动快速失败",
            "4" * 40,
            "chore: 完善用户部署与初始化流程",
            "5" * 40,
            "fix: 修复登录页样式问题",
            "6" * 40,
            "refactor!: 统一运行数据目录",
            "7" * 40,
            "feat: 已通过 PR 合并",
        )
        git_output = "\0".join((*commits, "")).encode()
        api_results = iter(("0\n", "0\n", "0\n", "0\n", "0\n", "0\n", "1\n"))
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[0] == "git":
                return self._completed(command, git_output)
            return self._completed(command, next(api_results))

        notes = build_release_change_notes(
            repository="IceeAn/codebuddy2api",
            repository_url="https://github.com/IceeAn/codebuddy2api",
            tag="v0.1.0",
            previous_tag="v0.0.1",
            generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
            run=run,
        )

        self.assertEqual(calls[0][:3], ["git", "log", "--reverse"])
        self.assertNotIn("### 合并的 PR", notes)
        self.assertEqual(notes.count("**Full Changelog**:"), 1)
        self.assertEqual(
            notes,
            """\
## 变更记录

<!-- Release notes generated using configuration in .github/release.yml at v0.1.0 -->

### 独立提交

#### 新功能

- [`3333333`](https://github.com/IceeAn/codebuddy2api/commit/3333333333333333333333333333333333333333) feat: 用户文件不存在时让启动快速失败

#### Bug 修复

- [`5555555`](https://github.com/IceeAn/codebuddy2api/commit/5555555555555555555555555555555555555555) fix: 修复登录页样式问题

#### 其他变更

- [`1111111`](https://github.com/IceeAn/codebuddy2api/commit/1111111111111111111111111111111111111111) style: 格式化前端文件
- [`2222222`](https://github.com/IceeAn/codebuddy2api/commit/2222222222222222222222222222222222222222) ci: 新增 GitHub Actions
- [`4444444`](https://github.com/IceeAn/codebuddy2api/commit/4444444444444444444444444444444444444444) chore: 完善用户部署与初始化流程
- [`6666666`](https://github.com/IceeAn/codebuddy2api/commit/6666666666666666666666666666666666666666) refactor!: 统一运行数据目录

**Full Changelog**: https://github.com/IceeAn/codebuddy2api/compare/v0.0.1...v0.1.0
""",
        )

    def test_wraps_generated_pr_notes_and_preserves_entry_order(self):
        generated_notes = """\
<!-- Release notes generated using configuration in .github/release.yml at v0.2.0 -->

## What's Changed
### 新功能
* 较早合并的功能 by @alice in https://github.com/owner/repo/pull/10
* 较晚合并的功能 by @bob in https://github.com/owner/repo/pull/20
## New Contributors
* @alice made their first contribution in https://github.com/owner/repo/pull/10

**Full Changelog**: https://github.com/owner/repo/compare/v0.1.0...v0.2.0
"""

        def run(command, **kwargs):
            return self._completed(command, b"")

        notes = build_release_change_notes(
            repository="owner/repo",
            repository_url="https://github.com/owner/repo",
            tag="v0.2.0",
            previous_tag="v0.1.0",
            generated_notes=generated_notes,
            run=run,
        )

        self.assertNotIn("### 独立提交", notes)
        self.assertNotIn("What's Changed", notes)
        self.assertIn("### 合并的 PR", notes)
        self.assertIn("#### 新功能", notes)
        self.assertIn("#### New Contributors", notes)
        self.assertLess(notes.index("较早合并的功能"), notes.index("较晚合并的功能"))
        self.assertLess(notes.index("较晚合并的功能"), notes.index("Full Changelog"))

    def test_omits_empty_categories_for_both_sources(self):
        sha = "a" * 40
        generated_notes = """\
## What's Changed
### 新功能

### Bug 修复
* 修复 PR by @alice in https://github.com/owner/repo/pull/12

### 其他变更

**Full Changelog**: https://github.com/owner/repo/compare/v1.0.0...v1.1.0
"""

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, f"{sha}\0docs: 更新文档\0".encode())
            return self._completed(command, "0\n")

        notes = build_release_change_notes(
            repository="owner/repo",
            repository_url="https://github.com/owner/repo",
            tag="v1.1.0",
            previous_tag="v1.0.0",
            generated_notes=generated_notes,
            run=run,
        )

        self.assertNotIn("#### 新功能", notes)
        self.assertEqual(notes.count("#### Bug 修复"), 1)
        self.assertEqual(notes.count("#### 其他变更"), 1)

    def test_renders_independent_commits_before_pull_requests(self):
        sha = "a" * 40
        generated_notes = """\
## What's Changed
### Bug 修复
* 修复 PR by @alice in https://github.com/owner/repo/pull/12

**Full Changelog**: https://github.com/owner/repo/compare/v1.0.0...v1.1.0
"""

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, f"{sha}\0docs: 更新文档\0".encode())
            return self._completed(command, "0\n")

        notes = build_release_change_notes(
            repository="owner/repo",
            repository_url="https://github.com/owner/repo",
            tag="v1.1.0",
            previous_tag="v1.0.0",
            generated_notes=generated_notes,
            run=run,
        )

        self.assertLess(notes.index("### 独立提交"), notes.index("### 合并的 PR"))
        self.assertLess(notes.index("### 合并的 PR"), notes.index("Full Changelog"))

    def test_omits_both_empty_source_sections(self):
        def run(command, **kwargs):
            return self._completed(command, b"")

        notes = build_release_change_notes(
            repository="owner/repo",
            repository_url="https://github.com/owner/repo",
            tag="v0.1.0",
            previous_tag=None,
            generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
            run=run,
        )

        self.assertNotIn("### 独立提交", notes)
        self.assertNotIn("### 合并的 PR", notes)
        self.assertTrue(notes.endswith("v0.0.1...v0.1.0\n"))

    def test_uses_requested_comparison_range_and_queries_each_commit(self):
        sha = "a" * 40
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "git":
                return self._completed(command, f"{sha}\0chore: 调整配置\0".encode())
            return self._completed(command, "0\n")

        build_release_change_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v1.1.0",
            previous_tag="v1.0.0",
            generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
            run=run,
        )

        self.assertEqual(calls[0][0][-1], "v1.0.0..v1.1.0")
        self.assertIn("--reverse", calls[0][0])
        self.assertIn(f"repos/owner/repo/commits/{sha}/pulls", calls[1][0])
        self.assertIn("--jq", calls[1][0])
        self.assertIn("length", calls[1][0])

    def test_first_release_includes_all_commits_reachable_from_tag(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return self._completed(command, b"")

        build_release_change_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v0.0.1",
            previous_tag=None,
            generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
            run=run,
        )

        self.assertEqual(calls[0][-1], "v0.0.1")

    def test_excludes_only_independent_release_commits(self):
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

        notes = build_release_change_notes(
            repository="owner/repo",
            repository_url="https://example.com/owner/repo",
            tag="v2.0.0",
            previous_tag="v1.0.0",
            generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
            run=run,
        )

        self.assertNotIn("发布 v1.0.0", notes)
        self.assertNotIn("发布 v2.0.0", notes)
        self.assertIn("chore(release-notes): 调整发布说明", notes)
        self.assertIn("chore: 更新版本号", notes)

    def test_requires_exactly_one_full_changelog(self):
        invalid_generated_notes = (
            "## What's Changed\n",
            "**Full Changelog**: https://example.com/first\n"
            "**Full Changelog**: https://example.com/second\n",
        )

        def run(command, **kwargs):
            return self._completed(command, b"")

        for generated_notes in invalid_generated_notes:
            with self.subTest(generated_notes=generated_notes):
                with self.assertRaisesRegex(RuntimeError, "Full Changelog"):
                    build_release_change_notes(
                        repository="owner/repo",
                        repository_url="https://example.com/owner/repo",
                        tag="v1.0.0",
                        previous_tag=None,
                        generated_notes=generated_notes,
                        run=run,
                    )

    def test_rejects_unsupported_generated_heading_level(self):
        generated_notes = """\
#### 意外标题
* 变更

**Full Changelog**: https://example.com/changelog
"""

        def run(command, **kwargs):
            return self._completed(command, b"")

        with self.assertRaisesRegex(RuntimeError, "标题层级"):
            build_release_change_notes(
                repository="owner/repo",
                repository_url="https://example.com/owner/repo",
                tag="v1.0.0",
                previous_tag=None,
                generated_notes=generated_notes,
                run=run,
            )

    def test_rejects_invalid_api_count(self):
        sha = "a" * 40

        def run(command, **kwargs):
            if command[0] == "git":
                return self._completed(command, f"{sha}\0fix: 修复问题\0".encode())
            return self._completed(command, "not-a-count\n")

        with self.assertRaisesRegex(RuntimeError, "GitHub API"):
            build_release_change_notes(
                repository="owner/repo",
                repository_url="https://example.com/owner/repo",
                tag="v1.0.0",
                previous_tag=None,
                generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
                run=run,
            )

    def test_subprocess_failures_are_not_hidden(self):
        with self.assertRaises(subprocess.CalledProcessError):
            build_release_change_notes(
                repository="owner/repo",
                repository_url="https://example.com/owner/repo",
                tag="v1.0.0",
                previous_tag=None,
                generated_notes=GENERATED_WITHOUT_PULL_REQUESTS,
                run=mock.Mock(
                    side_effect=subprocess.CalledProcessError(1, ["git", "log"])
                ),
            )


if __name__ == "__main__":
    unittest.main()
