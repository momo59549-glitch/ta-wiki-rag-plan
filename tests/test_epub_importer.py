import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from packages.evidence.epub_importer import import_epub, save_manifest


def create_epub(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr("OPS/book.opf", '''<package><metadata><dc:title xmlns:dc="x">测试书</dc:title><dc:language xmlns:dc="x">zh-CN</dc:language></metadata><manifest><item id="c1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>''')
        archive.writestr("OPS/chapter-1.xhtml", "<html><body><h1>第一章</h1><p>锤子线是一个形态。</p><script>ignore()</script></body></html>")


class EpubImporterTests(unittest.TestCase):
    def test_import_and_manifest(self):
        with TemporaryDirectory() as directory:
            source, manifest = Path(directory) / "book.epub", Path(directory) / "book.json"
            create_epub(source)
            result = import_epub(source)
            save_manifest(result, manifest)
            self.assertEqual(result.title, "测试书")
            self.assertEqual(len(result.chapters), 1)
            self.assertIn("锤子线", result.chapters[0].text)
            self.assertNotIn("ignore", result.chapters[0].text)
            self.assertTrue(manifest.exists())
