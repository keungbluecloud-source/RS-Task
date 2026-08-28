import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from .models import Chunk, SearchResult
from .text import cosine, hashed_vector, tokenize


class HybridIndex:
    def __init__(self, chunks: Iterable[Chunk]) -> None:
        self.chunks = list(chunks)
        self.tokens = [tokenize(" ".join((chunk.title, chunk.source, chunk.section or "", chunk.text)))
                       for chunk in self.chunks]
        self.term_frequencies = [Counter(tokens) for tokens in self.tokens]
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            self.document_frequency.update(set(tokens))
        self.average_length = sum(map(len, self.tokens)) / max(1, len(self.tokens))
        self.vectors = [hashed_vector(tokens) for tokens in self.tokens]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "chunks": [chunk.to_dict() for chunk in self.chunks]}
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "HybridIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("Unsupported index version")
        return cls(Chunk.from_dict(value) for value in payload["chunks"])

    def _bm25(self, query: List[str], position: int) -> float:
        score, k1, b = 0.0, 1.5, 0.75
        length = len(self.tokens[position])
        for term in set(query):
            frequency = self.term_frequencies[position].get(term, 0)
            if not frequency:
                continue
            n = self.document_frequency[term]
            inverse = math.log(1 + (len(self.chunks) - n + 0.5) / (n + 0.5))
            denominator = frequency + k1 * (1 - b + b * length / max(1, self.average_length))
            score += inverse * frequency * (k1 + 1) / denominator
        return score

    def search(self, query: str, limit: int = 6, candidate_limit: int = 15) -> List[SearchResult]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_vector = hashed_vector(query_tokens)
        lexical = [self._bm25(query_tokens, i) for i in range(len(self.chunks))]
        semantic = [cosine(query_vector, vector) for vector in self.vectors]
        lexical_rank = sorted(range(len(self.chunks)), key=lambda i: lexical[i], reverse=True)[:candidate_limit]
        semantic_rank = sorted(range(len(self.chunks)), key=lambda i: semantic[i], reverse=True)[:candidate_limit]
        fused: Dict[int, float] = defaultdict(float)
        for ranking in (lexical_rank, semantic_rank):
            for rank, position in enumerate(ranking, 1):
                fused[position] += 1.0 / (60 + rank)
        normalized_query = " ".join(query_tokens)
        for position in fused:
            section = " ".join(tokenize(self.chunks[position].section or ""))
            if section and section in normalized_query:
                fused[position] += 0.035
        order = sorted(fused, key=lambda i: (fused[i], lexical[i], semantic[i]), reverse=True)[:limit]
        return [SearchResult(self.chunks[i], fused[i], lexical[i], semantic[i]) for i in order]
