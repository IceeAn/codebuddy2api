"""安全发布约束必须随工作流和发行包保留。"""
import tests

import re
import unittest
from pathlib import Path

from scripts.build_release_package import REQUIRED_FILES


class SupplyChainTests(unittest.TestCase):
    def test_all_external_actions_are_pinned_to_commits(self):
        root = Path(__file__).resolve().parents[1]
        for workflow in (root / ".github/workflows").glob("*.yml"):
            for action in re.findall(r"uses:\s+(\S+)", workflow.read_text()):
                self.assertRegex(action, r"^[\w/-]+@[0-9a-f]{40}$", workflow.name)

    def test_ci_and_release_audit_dependencies_without_ignoring_failures(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("ci.yml", "release.yml"):
            content = (root / ".github/workflows" / name).read_text()
            self.assertIn(
                "pip_audit -r requirements.txt --vulnerability-service osv",
                content,
            )
            self.assertNotIn("--no-deps", content)
            self.assertNotIn("--disable-pip", content)
            self.assertIn("pnpm audit --audit-level low", content)
            self.assertNotIn("continue-on-error: true", content)
            self.assertNotIn('node-version: "24.11.1"', content)

    def test_runtime_requirements_pin_only_direct_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        requirements = {
            line.split("==", 1)[0]
            for line in (root / "requirements.txt").read_text().splitlines()
            if line and not line.startswith("#")
        }

        self.assertEqual(requirements, {
            "anyio",
            "fastapi",
            "httpx",
            "Jinja2",
            "psutil",
            "pydantic",
            "python-dotenv",
            "python-multipart",
            "tiktoken",
            "tokenizers",
            "uvicorn",
        })

    def test_release_includes_deployment_guide(self):
        self.assertIn("doc/公网部署与安全配置.md", REQUIRED_FILES)
        self.assertFalse(any(path.startswith("docs/") for path in REQUIRED_FILES))

    def test_readme_documents_public_boundary(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        for text in ("doc/公网部署与安全配置.md", "可信团队", "关闭默认引导"):
            self.assertIn(text, readme)
