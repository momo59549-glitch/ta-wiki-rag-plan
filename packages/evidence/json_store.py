"""适合少量授权书籍的本地 JSON 持久化。

此模块是 EvidenceRepository 的可选持久化适配器，不依赖 SQL 服务。写入先落到临时文件，
再通过 replace 原子切换，避免进程中断产生半份清单。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from .models import EvidenceSpan, PageRegion, SourceAsset, SourceEdition, SourcePage
from .service import EvidenceRepository, EvidenceValidationError


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _record(value: object) -> dict:
    return _json_value(asdict(value))  # type: ignore[arg-type, return-value]


class JsonEvidenceStore:
    format_version = 1

    @classmethod
    def save(cls, repository: EvidenceRepository, path: Path) -> None:
        payload = {
            "format_version": cls.format_version,
            "editions": [_record(item) for item in repository.editions.values()],
            "assets": [_record(item) for item in repository.assets.values()],
            "pages": [_record(item) for item in repository.pages.values()],
            "regions": [_record(item) for item in repository.regions.values()],
            "evidence": [_record(item) for item in repository.evidence.values()],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> EvidenceRepository:
        if not path.exists():
            return EvidenceRepository()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceValidationError(f"无法读取证据清单: {exc}") from exc
        if payload.get("format_version") != cls.format_version:
            raise EvidenceValidationError("不支持的证据清单格式版本")
        repository = EvidenceRepository()
        try:
            for item in payload["editions"]:
                entity = SourceEdition(**{**item, "id": UUID(item["id"]), "tenant_id": UUID(item["tenant_id"]), "created_at": datetime.fromisoformat(item["created_at"])})
                repository.editions[entity.id] = entity
            for item in payload["assets"]:
                entity = SourceAsset(**{**item, "id": UUID(item["id"]), "source_edition_id": UUID(item["source_edition_id"]), "created_at": datetime.fromisoformat(item["created_at"])})
                repository.assets[entity.id] = entity
                repository._asset_hashes[entity.sha256] = entity.id
            for item in payload["pages"]:
                entity = SourcePage(**{**item, "id": UUID(item["id"]), "source_edition_id": UUID(item["source_edition_id"]), "asset_id": UUID(item["asset_id"]), "created_at": datetime.fromisoformat(item["created_at"])})
                repository.pages[entity.id] = entity
                repository._page_positions.add((entity.source_edition_id, entity.pdf_page_index))
            for item in payload["regions"]:
                entity = PageRegion(**{**item, "id": UUID(item["id"]), "source_page_id": UUID(item["source_page_id"]), "bbox": tuple(item["bbox"])})
                repository.regions[entity.id] = entity
            for item in payload["evidence"]:
                entity = EvidenceSpan(**{**item, "id": UUID(item["id"]), "source_edition_id": UUID(item["source_edition_id"]), "source_page_id": UUID(item["source_page_id"]), "region_ids": tuple(UUID(region_id) for region_id in item["region_ids"]), "union_bbox": tuple(item["union_bbox"]), "supersedes_id": UUID(item["supersedes_id"]) if item["supersedes_id"] else None, "created_at": datetime.fromisoformat(item["created_at"])})
                repository.evidence[entity.id] = entity
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceValidationError(f"证据清单字段无效: {exc}") from exc
        return repository
