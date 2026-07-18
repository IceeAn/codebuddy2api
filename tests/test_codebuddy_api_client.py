import unittest
from unittest import mock

import httpx

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
            headers = client.generate_codebuddy_headers("token", user_id="user-id")

        self.assertEqual(client.base_url, "https://api.example")
        self.assertEqual(client.api_endpoint, "https://api.example")
        self.assertEqual(headers["X-Conversation-ID"], "uuid-value")
        self.assertEqual(headers["X-Conversation-Request-ID"], "hex-value")
        self.assertEqual(headers["X-Conversation-Message-ID"], "uuidvalue")
        self.assertEqual(headers["X-Request-ID"], "uuidvalue")
        self.assertEqual(headers["X-User-Id"], "user-id")

    def test_client_rejects_missing_user_id_and_adds_enterprise_headers(self):
        client = CodeBuddyAPIClient()

        with self.assertRaises(ValueError):
            client.generate_codebuddy_headers("token")

        headers = client.generate_codebuddy_headers(
            "token",
            user_id="user-id",
            enterprise_id="enterprise-1",
        )

        self.assertEqual(headers["X-Enterprise-Id"], "enterprise-1")
        self.assertEqual(headers["X-Tenant-Id"], "enterprise-1")

    def test_account_uid_overrides_manual_fallback_and_department_is_optional(self):
        client = CodeBuddyAPIClient()

        oauth_headers = client.generate_codebuddy_headers(
            "token",
            user_id="anonymous_12345678",
            account_uid="official-account",
            enterprise_id="enterprise-1",
            department_full_name="研发部 / Platform",
        )
        manual_headers = client.generate_codebuddy_headers(
            "token",
            user_id="anonymous_12345678",
        )

        self.assertEqual(oauth_headers["X-User-Id"], "official-account")
        self.assertEqual(
            oauth_headers["X-Department-Info"],
            "%E7%A0%94%E5%8F%91%E9%83%A8%20%2F%20Platform",
        )
        request = httpx.Request("GET", "https://example.com", headers=oauth_headers)
        self.assertEqual(
            request.headers["X-Department-Info"],
            "%E7%A0%94%E5%8F%91%E9%83%A8%20%2F%20Platform",
        )
        self.assertEqual(manual_headers["X-User-Id"], "anonymous_12345678")
        self.assertNotIn("X-Department-Info", manual_headers)

        with self.assertRaisesRegex(ValueError, "department_full_name"):
            client.generate_codebuddy_headers(
                "token",
                user_id="user",
                department_full_name="invalid\nheader",
            )


if __name__ == "__main__":
    unittest.main()
