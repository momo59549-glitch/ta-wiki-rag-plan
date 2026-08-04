from __future__ import annotations

from hashlib import sha256
import re
from typing import Iterable
from uuid import UUID

from .models import EvidenceSpan, PageRegion, SourceAsset, SourceEdition, SourcePage, new_id


class EvidenceValidationError(ValueError):
    pass


_MEDIA_SIGNATURES = {
    "application/pdf": b"%PDF-",
    "application/epub+zip": b"PK\x03\x04",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise EvidenceValidationError("bbox 必须是 [0,1] 内且 x0<x1、y0<y1 的归一化坐标")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class EvidenceRepository:
    """可替换的内存权威存储实现，明确了未来 PostgreSQL repository 的事务边界。"""

    def __init__(self) -> None:
        self.editions: dict[UUID, SourceEdition] = {}
        self.assets: dict[UUID, SourceAsset] = {}
        self.pages: dict[UUID, SourcePage] = {}
        self.regions: dict[UUID, PageRegion] = {}
        self.evidence: dict[UUID, EvidenceSpan] = {}
        self._asset_hashes: dict[str, UUID] = {}
        self._page_positions: set[tuple[UUID, int]] = set()

    def create_edition(self, tenant_id: UUID, title: str, language: str, **metadata: str | None) -> SourceEdition:
        if not title.strip() or not language.strip():
            raise EvidenceValidationError("title 和 language 不能为空")
        edition = SourceEdition(new_id(), tenant_id, title.strip(), language.strip(), **metadata)
        self.editions[edition.id] = edition
        return edition

    def register_asset(self, source_edition_id: UUID, filename: str, media_type: str, content: bytes) -> SourceAsset:
        if source_edition_id not in self.editions:
            raise EvidenceValidationError("source edition 不存在")
        if not content:
            raise EvidenceValidationError("不接受空文件")
        signature = _MEDIA_SIGNATURES.get(media_type)
        matches = content.startswith(signature) if isinstance(signature, bytes) else bool(signature and any(content.startswith(item) for item in signature))
        if not matches:
            raise EvidenceValidationError("文件魔数与声明的 MIME 类型不匹配")
        digest = sha256(content).hexdigest()
        if digest in self._asset_hashes:
            return self.assets[self._asset_hashes[digest]]
        asset = SourceAsset(new_id(), source_edition_id, digest, media_type, filename, len(content))
        self.assets[asset.id] = asset
        self._asset_hashes[digest] = asset.id
        return asset

    def add_page(self, source_edition_id: UUID, asset_id: UUID, *, pdf_page_index: int, printed_page_label: str | None = None, printed_page_numeric: int | None = None, page_section: str = "unknown", mapping_confidence: float = 0.0) -> SourcePage:
        asset = self.assets.get(asset_id)
        if not asset or asset.source_edition_id != source_edition_id:
            raise EvidenceValidationError("页面必须属于指定 source edition 的 asset")
        if pdf_page_index < 0 or (source_edition_id, pdf_page_index) in self._page_positions:
            raise EvidenceValidationError("pdf_page_index 必须非负且在版次内唯一")
        if not 0 <= mapping_confidence <= 1:
            raise EvidenceValidationError("mapping_confidence 必须在 [0,1]")
        page = SourcePage(new_id(), source_edition_id, asset_id, pdf_page_index, pdf_page_index + 1, printed_page_label, printed_page_numeric, page_section, mapping_confidence)
        self.pages[page.id] = page
        self._page_positions.add((source_edition_id, pdf_page_index))
        return page

    def add_region(self, source_page_id: UUID, bbox: tuple[float, float, float, float], kind: str, ocr_confidence: float | None = None) -> PageRegion:
        if source_page_id not in self.pages:
            raise EvidenceValidationError("source page 不存在")
        _validate_bbox(bbox)
        if ocr_confidence is not None and not 0 <= ocr_confidence <= 1:
            raise EvidenceValidationError("ocr_confidence 必须在 [0,1]")
        region = PageRegion(new_id(), source_page_id, bbox, kind, ocr_confidence)
        self.regions[region.id] = region
        return region

    def create_evidence(self, source_page_id: UUID, region_ids: Iterable[UUID], raw_text: str, content_type: str, *, review_status: str = "draft", supersedes_id: UUID | None = None) -> EvidenceSpan:
        page = self.pages.get(source_page_id)
        regions = tuple(region_ids)
        if not page or not regions or any(self.regions.get(item, None) is None or self.regions[item].source_page_id != source_page_id for item in regions):
            raise EvidenceValidationError("证据必须引用同一页面上的至少一个有效区域")
        if not _normalize(raw_text):
            raise EvidenceValidationError("证据文本不能为空")
        if supersedes_id is not None and supersedes_id not in self.evidence:
            raise EvidenceValidationError("supersedes_id 不存在")
        if supersedes_id is not None and self.evidence[supersedes_id].source_page_id != source_page_id:
            raise EvidenceValidationError("证据修订不能跨页面")
        boxes = [self.regions[item].bbox for item in regions]
        bbox = (min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes))
        prior_revision = self.evidence[supersedes_id].revision if supersedes_id else 0
        confidence_values = [self.regions[item].ocr_confidence for item in regions if self.regions[item].ocr_confidence is not None]
        span = EvidenceSpan(new_id(), page.source_edition_id, source_page_id, regions, prior_revision + 1, raw_text, _normalize(raw_text), bbox, content_type, sum(confidence_values) / len(confidence_values) if confidence_values else None, review_status, supersedes_id)
        self.evidence[span.id] = span
        return span

    def citation_label(self, evidence_id: UUID) -> str:
        evidence = self.evidence.get(evidence_id)
        if not evidence:
            raise EvidenceValidationError("evidence 不存在")
        page, edition = self.pages[evidence.source_page_id], self.editions[evidence.source_edition_id]
        book_page = page.printed_page_label or "未标印刷页"
        edition_text = f"，{edition.edition_label}" if edition.edition_label else ""
        return f"[{edition.title}{edition_text}，书内第 {book_page} 页（文件第 {page.physical_page_number} 页），证据 revision {evidence.revision}]"
