import httpx
import unittest

from apps.research_ui.api_client import ResearchApiClient


class ResearchUiClientTests(unittest.TestCase):
    def test_client_uses_versioned_api(self):
        requests = []
        def handler(request: httpx.Request):
            requests.append((request.method, request.url.path))
            if request.url.path == "/healthz":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, json=[])
        client = ResearchApiClient("http://test", transport=httpx.MockTransport(handler))
        self.assertEqual(client.health()["status"], "ok")
        client.cases()
        client.jobs()
        client.answer_wiki("乌云盖顶", provider_api_key="temporary-secret")
        client.wiki_status()
        self.assertIn(("GET", "/api/v1/research-cases"), requests)
        self.assertIn(("GET", "/api/v1/jobs"), requests)
        self.assertIn(("POST", "/api/v1/wiki/answer"), requests)
        self.assertIn(("GET", "/api/v1/wiki/status"), requests)

    def test_loopback_api_bypasses_environment_proxy(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
        local = ResearchApiClient("http://127.0.0.1:8000", transport=transport)
        localhost = ResearchApiClient("http://localhost:8000", transport=transport)
        remote = ResearchApiClient("https://research.example.com", transport=transport)

        self.assertFalse(local.uses_environment_proxy)
        self.assertFalse(localhost.uses_environment_proxy)
        self.assertTrue(remote.uses_environment_proxy)


if __name__ == "__main__":
    unittest.main()
