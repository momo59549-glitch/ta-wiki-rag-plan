import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfWriter

from packages.evidence.pdf_importer import import_pdf, save_manifest
from packages.evidence.service import EvidenceValidationError


class PdfImporterTests(unittest.TestCase):
    def test_import_and_manifest_preserve_page_indices(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "book.pdf"
            manifest = Path(directory) / "book.json"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=300)
            writer.add_metadata({"/Title": "Test Book", "/Author": "Test Author"})
            with source.open("wb") as stream:
                writer.write(stream)

            result = import_pdf(source)
            save_manifest(result, manifest)

            self.assertEqual(result.title, "Test Book")
            self.assertEqual(result.author, "Test Author")
            self.assertEqual(result.pages_total, 1)
            self.assertEqual(result.pages[0].pdf_page_index, 0)
            self.assertTrue(manifest.exists())

    def test_rejects_renamed_non_pdf(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "fake.pdf"
            source.write_bytes(b"not a pdf")
            with self.assertRaises(EvidenceValidationError):
                import_pdf(source)
