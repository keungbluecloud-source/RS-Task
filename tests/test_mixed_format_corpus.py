import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from nist_rag.index import HybridIndex
from nist_rag.knowledge_base import KnowledgeBaseStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS = PROJECT_ROOT / "test-documents"
EXPECTED_FILES = {
    "nist-ai-rmf-core.html",
    "nist-ai-rmf-faq.html",
    "nist-csf-2.0.pdf",
    "nist-ssdf-1.1.pdf",
    "spdx-licenses.json",
}
PDF_AVAILABLE = sys.platform == "darwin" or importlib.util.find_spec("pypdf") is not None


@unittest.skipUnless(CORPUS.is_dir() and PDF_AVAILABLE,
                     "downloaded test corpus and a PDF extractor are required")
class MixedFormatCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        present = {path.name for path in CORPUS.iterdir() if path.is_file()}
        if present != EXPECTED_FILES:
            raise unittest.SkipTest("the five-file test corpus is incomplete or contains extra files")
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.store = KnowledgeBaseStore(Path(cls.temporary_directory.name) / "knowledge_bases")
        cls.first_report = cls.store.ingest("mixed-formats", CORPUS)
        cls.second_report = cls.store.ingest("mixed-formats", CORPUS)
        cls.index = HybridIndex.load(cls.store.index_path("mixed-formats"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def test_all_five_documents_and_three_formats_ingest(self):
        self.assertEqual(self.first_report.files, 5)
        self.assertEqual(self.first_report.failed, 0)
        sources = {chunk.source for chunk in self.index.chunks}
        self.assertEqual(sources, EXPECTED_FILES)
        self.assertEqual({Path(source).suffix for source in sources}, {".pdf", ".html", ".json"})

    def test_second_ingestion_is_idempotent(self):
        self.assertEqual(self.second_report.unchanged, 5)
        self.assertEqual(self.second_report.added, 0)
        self.assertEqual(self.second_report.updated, 0)
        self.assertEqual(self.second_report.removed, 0)

    def test_pdf_retrieval(self):
        result = self.index.search("What are the six CSF Core Functions?", limit=1)[0]
        self.assertEqual(result.chunk.source, "nist-csf-2.0.pdf")
        self.assertIsNotNone(result.chunk.page)

    def test_ai_rmf_core_html_retrieval(self):
        query = "govern is a cross-cutting function infused throughout AI risk management"
        result = self.index.search(query, limit=1)[0]
        self.assertEqual(result.chunk.source, "nist-ai-rmf-core.html")
        self.assertEqual(result.chunk.section, "5.1 Govern")

    def test_ai_rmf_faq_html_retrieval(self):
        query = "Will organizations be required to use the AI RMF voluntary framework?"
        result = self.index.search(query, limit=1)[0]
        self.assertEqual(result.chunk.source, "nist-ai-rmf-faq.html")

    def test_json_retrieval(self):
        result = self.index.search("0BSD licenseId isOsiApproved", limit=1)[0]
        self.assertEqual(result.chunk.source, "spdx-licenses.json")


if __name__ == "__main__":
    unittest.main()
