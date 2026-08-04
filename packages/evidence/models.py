from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> UUID:
    return uuid4()


ReviewStatus = Literal["draft", "reviewed", "verified", "rejected"]
AssetStatus = Literal["quarantined", "processing", "active", "revoked", "deleted"]


@dataclass(frozen=True, slots=True)
class SourceEdition:
    id: UUID
    tenant_id: UUID
    title: str
    language: str
    edition_label: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    created_at: datetime = field(default_factory=now_utc)


@dataclass(frozen=True, slots=True)
class SourceAsset:
    id: UUID
    source_edition_id: UUID
    sha256: str
    media_type: Literal["application/pdf", "application/epub+zip", "image/png", "image/jpeg", "image/tiff"]
    filename: str
    byte_size: int
    status: AssetStatus = "quarantined"
    created_at: datetime = field(default_factory=now_utc)


@dataclass(frozen=True, slots=True)
class SourcePage:
    id: UUID
    source_edition_id: UUID
    asset_id: UUID
    pdf_page_index: int
    physical_page_number: int
    printed_page_label: str | None
    printed_page_numeric: int | None
    page_section: Literal["front_matter", "body", "appendix", "unknown"] = "unknown"
    mapping_confidence: float = 0.0
    created_at: datetime = field(default_factory=now_utc)


@dataclass(frozen=True, slots=True)
class PageRegion:
    id: UUID
    source_page_id: UUID
    # 归一化坐标，左上原点：x0,y0,x1,y1
    bbox: tuple[float, float, float, float]
    kind: Literal["paragraph", "caption", "table", "footnote", "heading"]
    ocr_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """不可变证据 revision；修订通过 supersedes_id 链接而非原地更新。"""

    id: UUID
    source_edition_id: UUID
    source_page_id: UUID
    region_ids: tuple[UUID, ...]
    revision: int
    raw_text: str
    normalized_text: str
    union_bbox: tuple[float, float, float, float]
    content_type: Literal["paragraph", "caption", "table", "footnote", "heading"]
    ocr_confidence: float | None
    review_status: ReviewStatus
    supersedes_id: UUID | None
    created_at: datetime = field(default_factory=now_utc)
