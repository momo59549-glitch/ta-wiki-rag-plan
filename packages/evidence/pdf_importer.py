"""Safe, page-addressable PDF text extraction for book evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .service import EvidenceValidationError


@dataclass(frozen=True, slots=True)
class PdfPage:
    pdf_page_index: int
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class PdfImportResult:
    source_path: str
    sha256: str
    title: str
    author: str | None
    pages_total: int
    pages_with_text: int
    characters: int
    pages: tuple[PdfPage, ...]


def _normalize_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = "".join(char for char in value if char in {"\n", "\t"} or ord(char) >= 32)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _metadata_text(metadata: Any, key: str) -> str | None:
    if not metadata:
        return None
    try:
        value = metadata.get(key)
    except AttributeError:
        return None
    text = str(value).strip() if value is not None else ""
    return text or None


def import_pdf(
    path: Path,
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
    max_pages: int = 5_000,
) -> PdfImportResult:
    """Extract embedded text while preserving zero-based PDF page locators."""
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise EvidenceValidationError("不是有效的 PDF 文件")
    if path.stat().st_size > max_file_bytes:
        raise EvidenceValidationError("PDF 文件超过安全大小限制")
    raw = path.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise EvidenceValidationError("PDF 文件头不正确")

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise EvidenceValidationError("缺少 pypdf；请安装 knowledge 可选依赖") from exc

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise EvidenceValidationError("PDF 已加密，不能导入；请提供合法的无加密版本")
        if len(reader.pages) > max_pages:
            raise EvidenceValidationError("PDF 页数超过安全限制")
        pages: list[PdfPage] = []
        for index, page in enumerate(reader.pages):
            text = _normalize_text(page.extract_text() or "")
            pages.append(PdfPage(index, index + 1, text))
    except EvidenceValidationError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise EvidenceValidationError(f"无法解析 PDF: {exc}") from exc

    pages_with_text = sum(bool(page.text) for page in pages)
    characters = sum(len(page.text) for page in pages)
    title = _metadata_text(reader.metadata, "/Title") or path.stem
    author = _metadata_text(reader.metadata, "/Author")
    return PdfImportResult(
        source_path=str(path),
        sha256=sha256(raw).hexdigest(),
        title=title,
        author=author,
        pages_total=len(pages),
        pages_with_text=pages_with_text,
        characters=characters,
        pages=tuple(pages),
    )


def save_manifest(result: PdfImportResult, path: Path) -> None:
    """Save text plus exact zero-based PDF page locators atomically."""
    payload = {
        "format_version": 1,
        "source_type": "pdf",
        "citation_locator": "pdf_page_index",
        **asdict(result),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
