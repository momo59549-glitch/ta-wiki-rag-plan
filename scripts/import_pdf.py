from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.evidence.pdf_importer import import_pdf, save_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="导入本地 PDF，生成逐页正文与 JSON 清单")
    parser.add_argument("file", type=Path, help="待导入的 PDF 文件")
    parser.add_argument("--output-dir", type=Path, default=Path("data/manifests"))
    parser.add_argument("--title", help="PDF 元数据缺失或错误时覆盖书名")
    args = parser.parse_args()
    result = import_pdf(args.file)
    if args.title:
        result = replace(result, title=args.title.strip())
    output = args.output_dir / f"{result.sha256}.pdf.json"
    save_manifest(result, output)
    coverage = result.pages_with_text / result.pages_total if result.pages_total else 0.0
    print(f"已导入《{result.title}》：{result.pages_total} 页，文字页覆盖率 {coverage:.1%}")
    print(f"正文字符：{result.characters}")
    print(f"清单：{output}")


if __name__ == "__main__":
    main()
