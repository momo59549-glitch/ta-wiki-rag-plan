"""标准库实现的安全 EPUB 元数据和正文提取器。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile, is_zipfile

from .service import EvidenceValidationError


@dataclass(frozen=True, slots=True)
class EpubChapter:
    order: int
    href: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class EpubImportResult:
    source_path: str
    sha256: str
    title: str
    language: str | None
    identifier: str | None
    chapters: tuple[EpubChapter, ...]


class _TextExtractor(HTMLParser):
    _BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}: self.ignored += 1
        if tag in self._BLOCK_TAGS: self.parts.append("\n")
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}: self.ignored = max(0, self.ignored - 1)
        if tag in self._BLOCK_TAGS: self.parts.append("\n")
    def handle_data(self, data: str) -> None:
        if not self.ignored: self.parts.append(data)
    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))).strip()


def _local_name(element: ET.Element, name: str) -> bool:
    return element.tag.rsplit("}", 1)[-1] == name


def _safe_member(base: PurePosixPath, href: str) -> str:
    path = (base / PurePosixPath(href.split("#", 1)[0])).as_posix()
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise EvidenceValidationError("EPUB 包含不安全的内部路径")
    return normalized.as_posix()


def import_epub(path: Path, *, max_entries: int = 5_000, max_uncompressed_bytes: int = 200 * 1024 * 1024) -> EpubImportResult:
    if not path.is_file() or path.suffix.lower() != ".epub" or not is_zipfile(path):
        raise EvidenceValidationError("不是有效的 EPUB 文件")
    raw = path.read_bytes()
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries or sum(item.file_size for item in entries) > max_uncompressed_bytes:
                raise EvidenceValidationError("EPUB 条目数或解压大小超过安全限制")
            if archive.read("mimetype") != b"application/epub+zip":
                raise EvidenceValidationError("EPUB mimetype 不正确")
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next((item.attrib.get("full-path") for item in container.iter() if _local_name(item, "rootfile")), None)
            if not rootfile:
                raise EvidenceValidationError("EPUB 缺少 OPF rootfile")
            opf_path = _safe_member(PurePosixPath("."), rootfile)
            opf = ET.fromstring(archive.read(opf_path))
            title = next((item.text or "" for item in opf.iter() if _local_name(item, "title")), path.stem)
            language = next((item.text for item in opf.iter() if _local_name(item, "language")), None)
            identifier = next((item.text for item in opf.iter() if _local_name(item, "identifier")), None)
            manifest = {item.attrib["id"]: item.attrib.get("href", "") for item in opf.iter() if _local_name(item, "item") and item.attrib.get("media-type") in {"application/xhtml+xml", "text/html"}}
            spine = [item.attrib.get("idref", "") for item in opf.iter() if _local_name(item, "itemref")]
            opf_base = PurePosixPath(opf_path).parent
            chapters: list[EpubChapter] = []
            for order, item_id in enumerate(spine, start=1):
                href = manifest.get(item_id)
                if not href:
                    continue
                internal_path = _safe_member(opf_base, href)
                extractor = _TextExtractor()
                extractor.feed(archive.read(internal_path).decode("utf-8", errors="replace"))
                text = extractor.text()
                if text:
                    chapters.append(EpubChapter(order, internal_path, text.split("\n", 1)[0][:120], text))
    except (BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise EvidenceValidationError(f"无法解析 EPUB: {exc}") from exc
    return EpubImportResult(str(path), sha256(raw).hexdigest(), title.strip() or path.stem, language, identifier, tuple(chapters))


def save_manifest(result: EpubImportResult, path: Path) -> None:
    """保存导入结果。EPUB 引用以章节路径定位，不能伪造为页码。"""
    payload = {"format_version": 1, "source_type": "epub", "citation_locator": "chapter_href", **asdict(result)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
