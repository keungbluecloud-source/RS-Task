from pathlib import Path

from .generation import answer
from .index import HybridIndex
from .ingestion import chunk_documents, load_corpus
from .models import Answer
from .knowledge_base import KnowledgeBaseStore


class Pipeline:
    def __init__(self, index: HybridIndex) -> None:
        self.index = index

    @classmethod
    def ingest(cls, documents: Path, index_path: Path) -> "Pipeline":
        index = HybridIndex(chunk_documents(load_corpus(documents)))
        index.save(index_path)
        return cls(index)

    @classmethod
    def open(cls, index_path: Path) -> "Pipeline":
        return cls(HybridIndex.load(index_path))

    @classmethod
    def open_knowledge_base(cls, name: str, store_root: Path = Path("data/knowledge_bases")) -> "Pipeline":
        return cls.open(KnowledgeBaseStore(store_root).index_path(name))

    def ask(self, query: str, limit: int = 6, use_llm: bool = True) -> Answer:
        query = query.strip()
        if not query:
            raise ValueError("Query must not be empty")
        if len(query) > 2000:
            raise ValueError("Query must be at most 2000 characters")
        lowered = query.lower()
        injection_markers = ("ignore previous instructions", "ignore all instructions",
                             "reveal the system prompt", "disregard the evidence")
        if any(marker in lowered for marker in injection_markers):
            raise ValueError("Query contains an instruction-override pattern")
        return answer(query, self.index.search(query, limit=limit), use_llm=use_llm)
