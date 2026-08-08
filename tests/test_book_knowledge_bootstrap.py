import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.knowledge.book_bootstrap import FIRST_CANDLESTICK_SEEDS, bootstrap_first_candlestick_cards
from packages.knowledge import FileKnowledgeRepository


class BookKnowledgeBootstrapTests(unittest.TestCase):
    def test_creates_evidence_backed_drafts_and_is_idempotent(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "book.json"
            manifest.write_text(
                json.dumps(
                    {
                        "source_type": "epub",
                        "citation_locator": "chapter_href",
                        "title": "测试书",
                        "sha256": "abc",
                        "chapters": [
                            {"title": seed.chapter_title, "href": f"chapter-{index}.xhtml"}
                            for index, seed in enumerate(FIRST_CANDLESTICK_SEEDS)
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            knowledge_root = root / "knowledge"

            first = bootstrap_first_candlestick_cards(manifest, knowledge_root)
            second = bootstrap_first_candlestick_cards(manifest, knowledge_root)

            self.assertEqual(len(first["created"]), len(FIRST_CANDLESTICK_SEEDS))
            self.assertEqual(second["created"], [])
            self.assertEqual(len(second["skipped"]), len(FIRST_CANDLESTICK_SEEDS))
            cards = FileKnowledgeRepository(knowledge_root).list_cards()
            self.assertTrue(all(card["status"] == "draft" for card in cards))
            self.assertTrue(all(card["evidence_refs"][0]["kind"] == "epub_chapter" for card in cards))


if __name__ == "__main__":
    unittest.main()
