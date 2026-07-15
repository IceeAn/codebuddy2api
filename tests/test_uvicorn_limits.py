import unittest

from src.uvicorn_limits import to_uvicorn_limit_concurrency


class UvicornLimitTests(unittest.TestCase):
    def test_converts_configured_capacity_to_uvicorn_threshold(self):
        self.assertIsNone(to_uvicorn_limit_concurrency(None))
        self.assertEqual(to_uvicorn_limit_concurrency(1), 2)
        self.assertEqual(to_uvicorn_limit_concurrency(100), 101)

    def test_rejects_values_outside_validated_config_domain(self):
        for value in (True, 0, -1, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    to_uvicorn_limit_concurrency(value)


if __name__ == "__main__":
    unittest.main()
