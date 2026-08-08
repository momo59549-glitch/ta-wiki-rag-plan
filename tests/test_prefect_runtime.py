import os
import unittest

from packages.orchestration.prefect_runtime import ensure_local_prefect_no_proxy


class PrefectRuntimeTests(unittest.TestCase):
    def test_adds_loopback_to_no_proxy_only_for_local_server(self):
        previous = {key: os.environ.get(key) for key in ("PREFECT_API_URL", "NO_PROXY", "no_proxy")}
        try:
            os.environ["PREFECT_API_URL"] = "http://127.0.0.1:4200/api"
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
            self.assertTrue(ensure_local_prefect_no_proxy())
            self.assertIn("127.0.0.1", os.environ["NO_PROXY"])
            self.assertIn("localhost", os.environ["no_proxy"])
            os.environ["PREFECT_API_URL"] = "https://prefect.example/api"
            self.assertFalse(ensure_local_prefect_no_proxy())
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
