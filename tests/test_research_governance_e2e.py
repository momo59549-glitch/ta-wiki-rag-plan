import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

import apps.api.main as api
from packages.governance import FileAuditLog
from packages.orchestration import CaseState, FileCaseStateMachine


class ResearchGovernanceEndToEndTests(unittest.TestCase):
    def test_case_dual_approval_rule_registry_and_knowledge_publication(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            previous = {name: getattr(api, name) for name in ("CASE_ROOT", "REGISTRY_ROOT", "KNOWLEDGE_ROOT", "EVIDENCE_ROOT", "AUDIT_PATH")}
            api.CASE_ROOT = root / "cases"
            api.REGISTRY_ROOT = root / "registry"
            api.KNOWLEDGE_ROOT = root / "knowledge"
            api.EVIDENCE_ROOT = root / "evidence"
            api.AUDIT_PATH = root / "audit.jsonl"
            case = api.CASE_ROOT / "case_1"
            machine = FileCaseStateMachine.create(case, "case_1")
            for state in (CaseState.DATA_READY, CaseState.OBSERVATIONS_READY, CaseState.OUTCOMES_READY, CaseState.HYPOTHESIS_DRAFTED, CaseState.BACKTEST_REVIEWED, CaseState.KNOWLEDGE_DRAFTED, CaseState.REPORT_READY, CaseState.QA_PASSED, CaseState.AWAITING_HYPOTHESIS_APPROVAL):
                machine.transition(state, f"e2e.{state.value}")
            (case / "case.json").write_text(json.dumps({"case_id": "case_1", "state": machine.state.value, "rule": {"id": "hammer", "version": "1.0.0", "semantic_hash": "sha256:rule"}, "dataset_snapshot_id": "sha256:data"}), encoding="utf-8")
            (case / "qa_review.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            (case / "hypothesis_draft.json").write_text(json.dumps({"candidate_horizons": [{"horizon_bars": 3}]}), encoding="utf-8")
            api.EVIDENCE_ROOT.mkdir()
            manifest = api.EVIDENCE_ROOT / "book.json"
            manifest.write_text(json.dumps({"source_type": "epub", "citation_locator": "chapter_href", "title": "蜡烛图证据", "sha256": "abc", "chapters": [{"href": "hammer.xhtml", "title": "锤子线"}]}, ensure_ascii=False), encoding="utf-8")
            client = TestClient(api.app)
            lead = {"X-TA-Actor": "lead-1", "X-TA-Role": "research_lead"}
            owner = {"X-TA-Actor": "owner-1", "X-TA-Role": "rule_owner"}
            editor = {"X-TA-Actor": "editor-1", "X-TA-Role": "knowledge_editor"}
            reviewer = {"X-TA-Actor": "reviewer-1", "X-TA-Role": "content_reviewer"}
            try:
                self.assertEqual(client.post("/api/v1/research-cases/case_1/approvals/hypothesis/request", headers=lead).status_code, 200)
                self.assertEqual(client.post("/api/v1/research-cases/case_1/approvals/hypothesis/review", headers=lead, json={"approver": "lead-1", "decision": "approve", "comment": "证据与限制已复核"}).status_code, 200)
                self.assertEqual(client.post("/api/v1/research-cases/case_1/approvals/rule/request", headers=owner).status_code, 200)
                self.assertEqual(client.post("/api/v1/research-cases/case_1/approvals/rule/review", headers=owner, json={"approver": "owner-1", "decision": "approve", "comment": "规则版本可进入研究注册表"}).status_code, 200)
                self.assertTrue((api.REGISTRY_ROOT / "hammer-1.0.0.json").is_file())
                created = client.post("/api/v1/knowledge-cards", headers=editor, json={"title": "锤子线证据卡", "claim": "形态定义必须结合上下文并人工复核。", "source_case_id": "case_1", "evidence_refs": [{"kind": "epub_chapter", "manifest_path": str(manifest), "locator": "hammer.xhtml"}], "research_artifacts": ["case_1/hypothesis_draft.json"], "limitations": ["不是投资建议"]})
                self.assertEqual(created.status_code, 201)
                card_id = Path(created.json()["artifact"]).stem
                published = client.post(f"/api/v1/knowledge-cards/{card_id}/review", headers=reviewer, json={"reviewer": "reviewer-1", "decision": "publish", "comment": "来源与案例审批链均已核验"})
                self.assertEqual(published.status_code, 200)
                self.assertTrue((api.KNOWLEDGE_ROOT / "published" / f"{card_id}.json").is_file())
                results = client.get("/api/v1/knowledge-search", params={"q": "锤子线"}).json()
                self.assertEqual(results[0]["card_id"], card_id)
                self.assertTrue(FileAuditLog(api.AUDIT_PATH).verify()["valid"])
            finally:
                for name, value in previous.items():
                    setattr(api, name, value)


if __name__ == "__main__":
    unittest.main()
