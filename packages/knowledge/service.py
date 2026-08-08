"""File-backed claim/evidence knowledge cards; research data remains primary."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from packages.evidence import JsonEvidenceStore
from packages.research.json_store import write_json


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    kind: Literal["evidence_span", "epub_chapter", "pdf_page"]
    manifest_path: str
    locator: str


class FileKnowledgeRepository:
    def __init__(self, root: Path):
        self.root = root

    def create_draft(self, *, title: str, claim: str, source_case_id: str | None, evidence_refs: list[EvidenceReference], research_artifacts: list[str], limitations: list[str]) -> Path:
        if not title.strip() or not claim.strip():
            raise KnowledgeError("title 和 claim 不能为空")
        if not evidence_refs and not research_artifacts:
            raise KnowledgeError("知识卡必须至少引用证据或研究工件")
        resolved = [self._resolve_reference(item) for item in evidence_refs]
        card_id = "kc_" + uuid4().hex
        payload = {
            "schema_version": "knowledge-card/v1", "card_id": card_id, "status": "draft",
            "title": title.strip(), "claim": claim.strip(), "source_case_id": source_case_id,
            "evidence_refs": resolved, "research_artifacts": research_artifacts,
            "limitations": limitations, "created_at": datetime.now(timezone.utc),
            "publication": "blocked_until_content_review",
        }
        payload["content_sha256"] = "sha256:" + sha256(json.dumps({k: v for k, v in payload.items() if k not in {"created_at", "content_sha256"}}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        target = self.root / "drafts" / f"{card_id}.json"
        write_json(target, payload)
        return target

    def get(self, card_id: str) -> dict:
        if not card_id.startswith("kc_") or not card_id.removeprefix("kc_").isalnum():
            raise KnowledgeError("知识卡 ID 非法")
        for directory in ("published", "rejected", "drafts"):
            path = self.root / directory / f"{card_id}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(card_id)

    def list_cards(self) -> list[dict]:
        by_id = {}
        for directory in ("drafts", "rejected", "published"):
            for path in (self.root / directory).glob("kc_*.json") if (self.root / directory).is_dir() else ():
                card = json.loads(path.read_text(encoding="utf-8"))
                by_id[card["card_id"]] = card
        return sorted(by_id.values(), key=lambda item: item["created_at"], reverse=True)

    def review(self, card_id: str, reviewer: str, decision: str, comment: str) -> Path:
        if decision not in {"publish", "reject", "request_changes"}:
            raise KnowledgeError("decision 仅支持 publish/reject/request_changes")
        if not reviewer.strip() or not comment.strip():
            raise KnowledgeError("审校人和说明不能为空")
        source = self.root / "drafts" / f"{card_id}.json"
        if not source.is_file():
            raise KnowledgeError("只允许审校 draft 知识卡")
        card = json.loads(source.read_text(encoding="utf-8"))
        if decision == "publish" and not card["evidence_refs"]:
            raise KnowledgeError("没有书籍/来源证据的知识卡不能发布；研究工件只能支撑经验层")
        card["status"] = "published" if decision == "publish" else "rejected" if decision == "reject" else "changes_requested"
        card["publication"] = "published" if decision == "publish" else "blocked"
        card["review"] = {"reviewer": reviewer.strip(), "decision": decision, "comment": comment.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat()}
        target_dir = "published" if decision == "publish" else "rejected" if decision == "reject" else "drafts"
        target = self.root / target_dir / source.name
        if target != source and target.exists():
            raise KnowledgeError("目标知识卡已存在")
        write_json(target, card)
        write_json(self.root / "reviews" / f"{card_id}.json", card["review"])
        if target != source:
            source.unlink()
        return target

    @staticmethod
    def _resolve_reference(reference: EvidenceReference) -> dict:
        manifest = Path(reference.manifest_path)
        if not manifest.is_file():
            raise KnowledgeError(f"证据清单不存在: {manifest}")
        if reference.kind == "evidence_span":
            repository = JsonEvidenceStore.load(manifest)
            try:
                evidence_id = UUID(reference.locator)
                evidence = repository.evidence[evidence_id]
            except (ValueError, KeyError) as exc:
                raise KnowledgeError("EvidenceSpan locator 无效") from exc
            if evidence.review_status not in {"reviewed", "verified"}:
                raise KnowledgeError("只有 reviewed/verified EvidenceSpan 可以支撑知识 claim")
            return {**asdict(reference), "citation": repository.citation_label(evidence_id), "revision": evidence.revision, "review_status": evidence.review_status}
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if reference.kind == "epub_chapter":
            if payload.get("source_type") != "epub" or payload.get("citation_locator") != "chapter_href":
                raise KnowledgeError("不是受支持的 EPUB 证据清单")
            chapter = next((item for item in payload.get("chapters", []) if item.get("href") == reference.locator), None)
            if not chapter:
                raise KnowledgeError("EPUB chapter locator 不存在")
            return {**asdict(reference), "citation": f"[{payload.get('title', 'EPUB')}，章节 {chapter.get('title') or chapter['href']}]", "source_sha256": payload.get("sha256")}
        if payload.get("source_type") != "pdf" or payload.get("citation_locator") != "pdf_page_index":
            raise KnowledgeError("不是受支持的 PDF 证据清单")
        try:
            page_index = int(reference.locator)
        except ValueError as exc:
            raise KnowledgeError("PDF page locator 必须是整数") from exc
        page = next((item for item in payload.get("pages", []) if item.get("pdf_page_index") == page_index), None)
        if not page or not str(page.get("text", "")).strip():
            raise KnowledgeError("PDF page locator 不存在或页面没有可核验正文")
        return {
            **asdict(reference),
            "citation": f"[{payload.get('title', 'PDF')}，PDF 第 {page.get('page_number', page_index + 1)} 页]",
            "source_sha256": payload.get("sha256"),
        }
