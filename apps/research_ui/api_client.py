from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


class ResearchApiClient:
    def __init__(self, base_url: str, *, actor: str = "local-development", role: str = "admin", api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        hostname = (urlparse(self.base_url).hostname or "").lower()
        self.uses_environment_proxy = hostname not in {"127.0.0.1", "localhost", "::1"}
        headers = {"X-TA-Actor": actor, "X-TA-Role": role}
        if api_key:
            headers["X-TA-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=15.0,
            transport=transport,
            headers=headers,
            trust_env=self.uses_environment_proxy,
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.text

    def health(self):
        return self._request("GET", "/healthz")

    def cases(self):
        return self._request("GET", "/api/v1/research-cases")

    def case(self, case_id: str):
        return self._request("GET", f"/api/v1/research-cases/{case_id}")

    def timeline(self, case_id: str):
        return self._request("GET", f"/api/v1/research-cases/{case_id}/timeline")

    def report(self, case_id: str):
        return self._request("GET", f"/api/v1/research-cases/{case_id}/report")

    def jobs(self):
        return self._request("GET", "/api/v1/jobs")

    def create_job(self, kind: str, payload: dict, idempotency_key: str | None = None):
        return self._request("POST", "/api/v1/jobs", json={"kind": kind, "payload": payload, "idempotency_key": idempotency_key})

    def cancel_job(self, job_id: str):
        return self._request("POST", f"/api/v1/jobs/{job_id}/cancel")

    def request_approval(self, case_id: str, stage: str):
        return self._request("POST", f"/api/v1/research-cases/{case_id}/approvals/{stage}/request")

    def review(self, case_id: str, stage: str, approver: str, decision: str, comment: str):
        return self._request("POST", f"/api/v1/research-cases/{case_id}/approvals/{stage}/review", json={"approver": approver, "decision": decision, "comment": comment})

    def knowledge_cards(self):
        return self._request("GET", "/api/v1/knowledge-cards")

    def search_knowledge(self, query: str, top_k: int = 5):
        return self._request("GET", "/api/v1/knowledge-search", params={"q": query, "top_k": top_k})

    def answer_wiki(self, question: str, top_k: int = 3, use_model: bool = True, provider_api_key: str | None = None):
        payload = {"question": question, "top_k": top_k, "use_model": use_model}
        if provider_api_key:
            payload["provider_api_key"] = provider_api_key
        return self._request("POST", "/api/v1/wiki/answer", json=payload)

    def wiki_status(self):
        return self._request("GET", "/api/v1/wiki/status")

    def review_knowledge(self, card_id: str, reviewer: str, decision: str, comment: str):
        return self._request("POST", f"/api/v1/knowledge-cards/{card_id}/review", json={"reviewer": reviewer, "decision": decision, "comment": comment})
