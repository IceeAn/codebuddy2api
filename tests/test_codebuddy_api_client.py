import unittest
from unittest import mock

from src.codebuddy_api_client import (
    CodeBuddyAPIClient,
    _safe_domain_header,
    _stainless_arch,
    _stainless_os,
)


class CodeBuddyAPIClientTests(unittest.TestCase):
    def test_domain_header_accepts_safe_value_and_rejects_empty_or_unsafe_values(self):
        self.assertEqual(_safe_domain_header(" codebuddy.example ", "fallback"), "codebuddy.example")
        self.assertEqual(_safe_domain_header("", "fallback"), "fallback")
        self.assertEqual(_safe_domain_header(None, "fallback"), "fallback")
        self.assertEqual(_safe_domain_header("bad\r\nheader", "fallback"), "fallback")

    def test_stainless_arch_normalizes_known_and_unknown_architectures(self):
        cases = [
            ("arm64", "arm64"),
            ("aarch64", "arm64"),
            ("x86_64", "x64"),
            ("AMD64", "x64"),
            ("riscv64", "riscv64"),
            ("", "x64"),
        ]

        for machine, expected in cases:
            with self.subTest(machine=machine):
                with mock.patch("src.codebuddy_api_client.platform.machine", return_value=machine):
                    self.assertEqual(_stainless_arch(), expected)

    def test_stainless_os_normalizes_known_and_unknown_systems(self):
        cases = [
            ("Darwin", "MacOS"),
            ("Linux", "Linux"),
            ("Windows", "Windows"),
            ("FreeBSD", "FreeBSD"),
            ("", "Linux"),
        ]

        for system, expected in cases:
            with self.subTest(system=system):
                with mock.patch("src.codebuddy_api_client.platform.system", return_value=system):
                    self.assertEqual(_stainless_os(), expected)

    def test_client_uses_configured_endpoint_and_generates_default_identifiers(self):
        with (
            mock.patch("config.get_codebuddy_api_endpoint", return_value="https://api.example"),
            mock.patch("config.get_codebuddy_api_host", return_value="api.example"),
            mock.patch("src.codebuddy_api_client.uuid.uuid4", return_value="uuid-value"),
            mock.patch("src.codebuddy_api_client.secrets.token_hex", return_value="hex-value"),
        ):
            client = CodeBuddyAPIClient()
            headers = client.generate_codebuddy_headers("token")

        self.assertEqual(client.base_url, "https://api.example")
        self.assertEqual(client.api_endpoint, "https://api.example")
        self.assertEqual(headers["X-Conversation-ID"], "uuid-value")
        self.assertEqual(headers["X-Conversation-Request-ID"], "hex-value")
        self.assertEqual(headers["X-Conversation-Message-ID"], "uuidvalue")
        self.assertEqual(headers["X-Request-ID"], "uuidvalue")
        self.assertEqual(headers["X-User-Id"], "b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b")


if __name__ == "__main__":
    unittest.main()
