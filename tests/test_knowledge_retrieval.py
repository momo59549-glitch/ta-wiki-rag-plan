import unittest

from packages.knowledge import search_published_cards


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_only_published_cards_are_retrieved(self):
        cards = [
            {"card_id": "kc_1", "status": "published", "title": "hammer pattern", "claim": "hammer reversal evidence", "evidence_refs": [{"citation": "book chapter"}], "limitations": []},
            {"card_id": "kc_2", "status": "draft", "title": "hammer draft", "claim": "unreviewed", "evidence_refs": [], "limitations": []},
        ]
        results = search_published_cards(cards, "hammer", 5)
        self.assertEqual([item["card_id"] for item in results], ["kc_1"])

    def test_chinese_short_query_ranks_matching_title_first(self):
        cards = [
            {"card_id": "kc_dark_cloud", "status": "published", "title": "乌云盖顶形态", "claim": "上涨后的看跌反转警告", "evidence_refs": [{"citation": "书籍章节"}], "limitations": []},
            {"card_id": "kc_hammer", "status": "published", "title": "锤子线与上吊线", "claim": "结合趋势判断", "evidence_refs": [{"citation": "书籍章节"}], "limitations": []},
            {"card_id": "kc_draft", "status": "draft", "title": "乌云盖顶草稿", "claim": "未审校", "evidence_refs": [], "limitations": []},
        ]

        results = search_published_cards(cards, "乌云盖顶", 2)

        self.assertEqual(results[0]["card_id"], "kc_dark_cloud")
        self.assertGreater(results[0]["score"], 0)
        self.assertNotIn("kc_draft", [item["card_id"] for item in results])

    def test_direct_pattern_name_beats_prefixed_related_pattern(self):
        cards = [
            {"card_id": "kc_hammer", "status": "published", "title": "锤子线与上吊线", "claim": "下跌后的锤子线", "evidence_refs": [], "limitations": []},
            {"card_id": "kc_inverted", "status": "published", "title": "流星线与倒锤子线", "claim": "下降后的倒锤子线", "evidence_refs": [], "limitations": []},
        ]

        results = search_published_cards(cards, "锤子线", 2)

        self.assertEqual(results[0]["card_id"], "kc_hammer")

        sentence_results = search_published_cards(cards, "锤子线需要什么条件？", 2)
        self.assertEqual(sentence_results[0]["card_id"], "kc_hammer")


if __name__ == "__main__":
    unittest.main()
