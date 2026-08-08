import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.knowledge import EvidenceReference, FileKnowledgeRepository, KnowledgeError


class KnowledgeCardTests(unittest.TestCase):
    def test_pdf_page_reference_is_resolved_and_publishable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "book.pdf.json"
            manifest.write_text(json.dumps({"source_type": "pdf", "citation_locator": "pdf_page_index", "title": "PDF书", "sha256": "def", "pages": [{"pdf_page_index": 4, "page_number": 5, "text": "可核验正文"}]}), encoding="utf-8")
            repository = FileKnowledgeRepository(root / "knowledge")
            path = repository.create_draft(title="规则", claim="可核验规则", source_case_id=None, evidence_refs=[EvidenceReference("pdf_page", str(manifest), "4")], research_artifacts=[], limitations=[])
            card_id = json.loads(path.read_text(encoding="utf-8"))["card_id"]
            published = repository.review(card_id, "content-reviewer", "publish", "PDF 页码及正文已复核")
            card = json.loads(published.read_text(encoding="utf-8"))
            self.assertIn("PDF 第 5 页", card["evidence_refs"][0]["citation"])

    def test_epub_chapter_reference_is_resolved_and_publishable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "book.json"
            manifest.write_text(json.dumps({"source_type": "epub", "citation_locator": "chapter_href", "title": "书", "sha256": "abc", "chapters": [{"href": "c1.xhtml", "title": "第一章", "text": "正文"}]}), encoding="utf-8")
            repository = FileKnowledgeRepository(root / "knowledge")
            path = repository.create_draft(title="形态", claim="一个可核验定义", source_case_id=None, evidence_refs=[EvidenceReference("epub_chapter", str(manifest), "c1.xhtml")], research_artifacts=[], limitations=[])
            card_id = json.loads(path.read_text(encoding="utf-8"))["card_id"]
            published = repository.review(card_id, "content-reviewer", "publish", "引用已复核")
            self.assertIn("章节 第一章", json.loads(published.read_text(encoding="utf-8"))["evidence_refs"][0]["citation"])
            self.assertFalse(path.exists())

    def test_research_only_card_cannot_publish_as_book_knowledge(self):
        with TemporaryDirectory() as temp:
            repository = FileKnowledgeRepository(Path(temp))
            path = repository.create_draft(title="经验", claim="研究经验", source_case_id="case_1", evidence_refs=[], research_artifacts=["outcomes.jsonl"], limitations=[])
            card_id = json.loads(path.read_text(encoding="utf-8"))["card_id"]
            with self.assertRaises(KnowledgeError):
                repository.review(card_id, "reviewer", "publish", "缺少证据")


if __name__ == "__main__":
    unittest.main()
