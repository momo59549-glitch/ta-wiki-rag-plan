from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import apps.api.main as api
from packages.knowledge import EvidenceReference, FileKnowledgeRepository


class WikiAnswerApiTests(unittest.TestCase):
    def test_answer_endpoint_uses_only_published_cards(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "book.json"
            manifest.write_text('{"source_type":"epub","citation_locator":"chapter_href","title":"书","sha256":"abc","chapters":[{"href":"dark.xhtml","title":"乌云盖顶形态"}]}', encoding="utf-8")
            api.KNOWLEDGE_ROOT = root / "knowledge"
            api.AUDIT_PATH = root / "audit.jsonl"
            repository = FileKnowledgeRepository(api.KNOWLEDGE_ROOT)
            draft = repository.create_draft(title="乌云盖顶形态", claim="上涨背景中的条件性看跌警告", source_case_id=None, evidence_refs=[EvidenceReference("epub_chapter", str(manifest), "dark.xhtml")], research_artifacts=[], limitations=["不保证反转"])
            card_id = repository.list_cards()[0]["card_id"]
            repository.review(card_id, "reviewer", "publish", "章节已核验")

            response = TestClient(api.app).post("/api/v1/wiki/answer", json={"question": "乌云盖顶", "use_model": False})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "answered")
            self.assertEqual(payload["evidence"][0]["card_id"], card_id)

    def test_per_request_provider_key_is_used_but_never_returned(self):
        class FakeAnswerer:
            model = "fake-model"

            def generate(self, question, evidence):
                return f"基于{evidence[0]['title']}回答"

        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "book.json"
            manifest.write_text(
                '{"source_type":"epub","citation_locator":"chapter_href","title":"书","sha256":"abc",'
                '"chapters":[{"href":"dark.xhtml","title":"乌云盖顶形态"}]}',
                encoding="utf-8",
            )
            api.KNOWLEDGE_ROOT = root / "knowledge"
            api.AUDIT_PATH = root / "audit.jsonl"
            repository = FileKnowledgeRepository(api.KNOWLEDGE_ROOT)
            repository.create_draft(
                title="乌云盖顶形态",
                claim="上涨背景中的条件性看跌警告",
                source_case_id=None,
                evidence_refs=[EvidenceReference("epub_chapter", str(manifest), "dark.xhtml")],
                research_artifacts=[],
                limitations=["不保证反转"],
            )
            card_id = repository.list_cards()[0]["card_id"]
            repository.review(card_id, "reviewer", "publish", "章节已核验")
            secret = "temporary-provider-secret"

            with patch.object(api.AnthropicWikiAnswerer, "from_credentials", return_value=FakeAnswerer()) as factory:
                response = TestClient(api.app).post(
                    "/api/v1/wiki/answer",
                    json={"question": "乌云盖顶", "use_model": True, "provider_api_key": secret},
                )

            self.assertEqual(response.status_code, 200)
            factory.assert_called_once_with(secret)
            self.assertNotIn(secret, response.text)
            self.assertNotIn(secret, api.AUDIT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
