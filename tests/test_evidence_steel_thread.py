import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from packages.evidence import EvidenceRepository, EvidenceValidationError, JsonEvidenceStore


class EvidenceSteelThreadTests(unittest.TestCase):
    def setUp(self):
        self.repo = EvidenceRepository()
        self.edition = self.repo.create_edition(uuid4(), "日本蜡烛图技术", "zh-CN", edition_label="第一版")
        self.asset = self.repo.register_asset(self.edition.id, "sample.pdf", "application/pdf", b"%PDF-1.7 synthetic")
        self.page = self.repo.add_page(self.edition.id, self.asset.id, pdf_page_index=27, printed_page_label="15", printed_page_numeric=15, page_section="body", mapping_confidence=0.97)

    def test_immutable_evidence_revision_and_citation(self):
        region = self.repo.add_region(self.page.id, (0.1, 0.2, 0.8, 0.4), "paragraph", 0.94)
        original = self.repo.create_evidence(self.page.id, [region.id], "锤子线出现在下跌背景中。", "paragraph")
        revised = self.repo.create_evidence(self.page.id, [region.id], "锤子线通常出现在下跌背景中。", "paragraph", review_status="reviewed", supersedes_id=original.id)
        self.assertEqual(original.revision, 1)
        self.assertEqual(revised.revision, 2)
        self.assertEqual(revised.supersedes_id, original.id)
        self.assertIn("书内第 15 页（文件第 28 页）", self.repo.citation_label(revised.id))

    def test_asset_hash_deduplicates_and_magic_is_checked(self):
        duplicate = self.repo.register_asset(self.edition.id, "copy.pdf", "application/pdf", b"%PDF-1.7 synthetic")
        self.assertEqual(duplicate.id, self.asset.id)
        with self.assertRaises(EvidenceValidationError):
            self.repo.register_asset(self.edition.id, "fake.pdf", "application/pdf", b"not a pdf")

    def test_cross_page_region_is_rejected(self):
        region = self.repo.add_region(self.page.id, (0.1, 0.2, 0.8, 0.4), "paragraph")
        other_page = self.repo.add_page(self.edition.id, self.asset.id, pdf_page_index=28)
        other_region = self.repo.add_region(other_page.id, (0.1, 0.2, 0.8, 0.4), "paragraph")
        with self.assertRaises(EvidenceValidationError):
            self.repo.create_evidence(self.page.id, [region.id, other_region.id], "不能跨页", "paragraph")

    def test_json_manifest_persists_without_database(self):
        region = self.repo.add_region(self.page.id, (0.1, 0.2, 0.8, 0.4), "paragraph", 0.94)
        evidence = self.repo.create_evidence(self.page.id, [region.id], "可回溯的证据。", "paragraph")
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "evidence-manifest.json"
            JsonEvidenceStore.save(self.repo, manifest)
            restored = JsonEvidenceStore.load(manifest)
        self.assertEqual(restored.evidence[evidence.id].normalized_text, "可回溯的证据。")
        self.assertIn("文件第 28 页", restored.citation_label(evidence.id))
