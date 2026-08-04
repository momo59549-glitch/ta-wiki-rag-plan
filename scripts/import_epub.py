from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.evidence.epub_importer import import_epub, save_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="导入本地 EPUB，生成章节文本与 JSON 清单")
    parser.add_argument("file", type=Path, help="data/books 下的 EPUB 文件")
    parser.add_argument("--output-dir", type=Path, default=Path("data/manifests"))
    args = parser.parse_args()
    result = import_epub(args.file)
    output = args.output_dir / f"{result.sha256}.epub.json"
    save_manifest(result, output)
    print(f"已导入《{result.title}》：{len(result.chapters)} 个章节")
    print(f"清单：{output}")


if __name__ == "__main__":
    main()
