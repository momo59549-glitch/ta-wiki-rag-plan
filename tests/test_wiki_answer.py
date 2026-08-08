from types import SimpleNamespace
import unittest

from packages.knowledge import AnthropicWikiAnswerer, answer_wiki_question, wiki_model_status


def card(card_id="kc_dark", status="published", title="乌云盖顶形态"):
    return {
        "card_id": card_id,
        "status": status,
        "title": title,
        "claim": "上涨背景中，第二根黑色实体回落并深入前一白色实体，是潜在看跌警告。",
        "evidence_refs": [{"kind": "epub_chapter", "locator": "chapter.xhtml", "citation": "[书，章节 乌云盖顶形态]", "source_sha256": "abc", "manifest_path": "private/path.json"}],
        "limitations": ["不保证反转，不构成交易指令。"],
    }


class FakeMessages:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="这是条件性看跌警告。[证据1]")])


class WikiAnswerTests(unittest.TestCase):
    def test_status_never_returns_credentials(self):
        status = wiki_model_status()
        self.assertNotIn("api_key", status)
        self.assertIn("credentials_configured", status)

    def test_empty_explicit_credentials_are_rejected(self):
        with self.assertRaises(Exception):
            AnthropicWikiAnswerer.from_credentials("   ")

    def test_extractive_answer_has_public_citation_without_manifest_path(self):
        result = answer_wiki_question([card()], "乌云盖顶", top_k=1)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["generation_mode"], "extractive")
        self.assertEqual(result["citations"][0]["citation"], "[书，章节 乌云盖顶形态]")
        self.assertNotIn("manifest_path", result["citations"][0])

    def test_unrelated_or_draft_evidence_is_refused(self):
        self.assertEqual(answer_wiki_question([card(status="draft")], "乌云盖顶")["status"], "insufficient_evidence")
        self.assertEqual(answer_wiki_question([card()], "量子计算财报预测")["status"], "insufficient_evidence")
        self.assertEqual(answer_wiki_question([card()], "量子计算公司的下一季利润是多少？")["status"], "insufficient_evidence")

    def test_anthropic_adapter_receives_only_curated_evidence(self):
        messages = FakeMessages()
        answerer = AnthropicWikiAnswerer("deepseek-v4-flash", SimpleNamespace(messages=messages))
        result = answer_wiki_question([card()], "乌云盖顶", top_k=1, answerer=answerer)
        self.assertEqual(result["generation_mode"], "llm_grounded")
        request = messages.requests[0]
        self.assertEqual(request["model"], "deepseek-v4-flash")
        prompt = request["messages"][0]["content"][0]["text"]
        self.assertIn("已审校主张", prompt)
        self.assertNotIn("private/path.json", prompt)

    def test_exact_pattern_question_excludes_similar_opposite_pattern(self):
        opposite = card("kc_piercing", title="刺透形态")
        opposite["claim"] = "下降背景中，第二根白色实体反弹并深入前一黑色实体，是潜在看涨警告。"
        opposite["limitations"] = ["必须有此前下降背景。"]

        result = answer_wiki_question([card(), opposite], "乌云盖顶形态是什么？", top_k=3)

        self.assertEqual([item["card_id"] for item in result["evidence"]], ["kc_dark"])
        self.assertNotIn("此前下降背景", " ".join(result["limitations"]))

    def test_model_failure_falls_back_without_exposing_exception_text(self):
        class BrokenMessages:
            def create(self, **kwargs):
                raise RuntimeError("secret-bearing-provider-message")

        answerer = AnthropicWikiAnswerer("deepseek-v4-flash", SimpleNamespace(messages=BrokenMessages()))
        result = answer_wiki_question([card()], "乌云盖顶", top_k=1, answerer=answerer)
        self.assertEqual(result["generation_mode"], "extractive_fallback")
        self.assertNotIn("secret-bearing-provider-message", str(result))


if __name__ == "__main__":
    unittest.main()
