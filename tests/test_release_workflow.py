import re
import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repository_root = Path(__file__).resolve().parents[1]
        cls.workflow = (
            repository_root / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

    def _step(self, name):
        match = re.search(
            rf"^      - name: {re.escape(name)}\n(?P<body>.*?)(?=^      - name:|^  [a-z_]+:|\Z)",
            self.workflow,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"缺少工作流步骤：{name}")
        return match.group("body")

    def test_release_blocks_unfixed_vulnerabilities_by_default(self):
        self.assertRegex(
            self.workflow,
            r"(?ms)^      ignore_unfixed:\n"
            r"        .*?default: false\n"
            r"        type: boolean$",
        )

        resolve_release = self._step("Resolve release target")
        self.assertIn('ignore_unfixed="false"', resolve_release)

        for step_name in (
            "Report HIGH and CRITICAL vulnerabilities",
            "Fail on CRITICAL vulnerabilities",
        ):
            step = self._step(step_name)
            self.assertIn(
                "ignore-unfixed: ${{ needs.resolve.outputs.ignore_unfixed }}", step
            )
            self.assertIn("TRIVY_USERNAME: ${{ github.actor }}", step)
            self.assertIn("TRIVY_PASSWORD: ${{ secrets.GITHUB_TOKEN }}", step)

    def test_release_versions_are_validated_before_build_jobs(self):
        validation = self._step("Validate release versions")
        self.assertIn(
            'python3 scripts/build_release_package.py "${TAG}" --validate-only',
            validation,
        )
        resolve_job = self.workflow.split("\n  backend:\n", maxsplit=1)[0]
        self.assertIn("- name: Validate release versions", resolve_job)

    def test_release_package_uses_tag_commit_timestamp(self):
        timestamp = self._step("Resolve release archive timestamp")
        self.assertIn('git show -s --format=%ct "${TAG}^{commit}"', timestamp)
        self.assertIn('echo "source_date_epoch=${source_date_epoch}"', timestamp)

        build = self._step("Build release package")
        self.assertIn(
            "SOURCE_DATE_EPOCH: ${{ steps.release_archive.outputs.source_date_epoch }}",
            build,
        )

    def test_scans_the_pushed_digest_and_does_not_rebuild_during_publish(self):
        scan_build = self._step("Build and push Docker image by digest")
        self.assertNotIn("tags:", scan_build)
        self.assertIn(
            "outputs: type=image,name=${{ needs.resolve.outputs.image }},"
            "push-by-digest=true,name-canonical=true,push=true",
            scan_build,
        )
        self.assertIn("push-by-digest=true", scan_build)
        self.assertIn("name-canonical=true", scan_build)
        self.assertIn("sbom: true", scan_build)
        self.assertIn("provenance: mode=max", scan_build)

        digest_reference = (
            "image-ref: ${{ needs.resolve.outputs.image }}@"
            "${{ needs.build_image.outputs.digest }}"
        )
        self.assertEqual(self.workflow.count(digest_reference), 2)

        publish_job = self.workflow.split("\n  publish:\n", maxsplit=1)[1]
        self.assertNotIn("docker/build-push-action", publish_job)
        self.assertIn("- build_image", publish_job)
        self.assertEqual(
            publish_job.count("DIGEST: ${{ needs.build_image.outputs.digest }}"),
            3,
        )
        self.assertRegex(
            publish_job,
            r"docker buildx imagetools create --prefer-index=false \\\n"
            r'\s+"\$\{tag_args\[@\]\}" "\$\{IMAGE\}@\$\{DIGEST\}"',
        )

    def test_builds_and_scans_every_supported_linux_platform(self):
        platforms = "linux/amd64,linux/arm64,linux/arm/v7"
        scan_build = self._step("Build and push Docker image by digest")
        self.assertIn(f"platforms: {platforms}", scan_build)

        qemu = self._step("Set up QEMU")
        buildx = self._step("Set up Docker Buildx")
        self.assertLess(self.workflow.index(qemu), self.workflow.index(buildx))

        scan_job = self.workflow.split("\n  scan:\n", maxsplit=1)[1].split(
            "\n  publish:\n", maxsplit=1
        )[0]
        self.assertIn(f"platform: [{platforms}]", scan_job)
        self.assertEqual(
            scan_job.count("TRIVY_PLATFORM: ${{ matrix.platform }}"), 2
        )

    def test_latest_markers_are_only_updated_for_highest_stable_tag(self):
        self.assertIn("latest: ${{ steps.latest.outputs.latest }}", self.workflow)
        self.assertIn("group: release-${{ github.repository }}", self.workflow)

        determine_latest = self._step(
            "Determine whether this is the highest stable tag"
        )
        self.assertIn("LC_ALL=C sort -V", determine_latest)
        self.assertIn(
            'if [[ "${TAG}" == "${highest_stable_tag}" ]]; then', determine_latest
        )

        publish_tags = self._step("Publish Docker image tags")
        self.assertIn('if [[ "${UPDATE_LATEST}" == "true" ]]; then', publish_tags)
        self.assertIn('tag_args+=(--tag "${IMAGE}:latest")', publish_tags)

        publish_release = self._step("Publish GitHub Release")
        self.assertIn('if [[ "${UPDATE_LATEST}" == "true" ]]; then', publish_release)
        self.assertIn("create_args+=(--latest)", publish_release)
        self.assertIn("create_args+=(--latest=false)", publish_release)
        self.assertIn("edit_args+=(--latest)", publish_release)
        self.assertIn("edit_args+=(--latest=false)", publish_release)

    def test_release_notes_api_has_authentication(self):
        comparison = self._step("Resolve previous stable tag")
        self.assertIn('echo "previous_tag=${previous_tag}"', comparison)

        build_notes = self._step("Build release notes")
        self.assertIn("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", build_notes)
        self.assertIn(
            "PREVIOUS_TAG: ${{ needs.resolve.outputs.previous_tag }}", build_notes
        )
        self.assertIn('previous_tag_name=${PREVIOUS_TAG}', build_notes)
        self.assertIn("scripts/build_release_change_notes.py", build_notes)
        self.assertIn('--generated-notes "${generated_notes}"', build_notes)
        self.assertIn('--previous-tag "${PREVIOUS_TAG}"', build_notes)
        self.assertIn('cat "${change_notes}"', build_notes)
        self.assertNotIn("direct_commit_notes", build_notes)

    def test_publish_can_read_pull_requests_for_release_change_notes(self):
        publish_job = self.workflow.split("\n  publish:\n", maxsplit=1)[1]
        permissions = publish_job.split("\n\n    steps:\n", maxsplit=1)[0]

        self.assertIn("pull-requests: read", permissions)

    def test_release_notes_use_product_facing_section_names(self):
        build_notes = self._step("Build release notes")

        self.assertIn('echo "## 版本说明"', build_notes)
        self.assertIn('cat "${change_notes}"', build_notes)
        self.assertNotIn('echo "## 手动说明"', build_notes)
        self.assertNotIn('echo "## 自动生成的变更"', build_notes)
        self.assertNotIn('echo "## 直接提交"', build_notes)
        self.assertNotIn('echo "## 本地运行包"', build_notes)
        self.assertNotIn("codebuddy2api.tar.gz", build_notes)
        self.assertNotIn("codebuddy2api.zip", build_notes)
        self.assertNotIn("SHA256SUMS.txt", build_notes)

    def test_pr_release_notes_have_three_categories(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = (repository_root / ".github" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("title: 新功能", config)
        self.assertIn("- enhancement", config)
        self.assertIn("title: Bug 修复", config)
        self.assertIn("- bug", config)
        self.assertIn("title: 其他变更", config)
        self.assertIn('- "*"', config)
        self.assertNotIn("skip-changelog", config)


if __name__ == "__main__":
    unittest.main()
