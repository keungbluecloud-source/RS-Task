from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Document:
    text: str
    source: str
    title: str
    kind: str
    page: Optional[int] = None
    section: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    knowledge_base_id: Optional[str] = None
    document_id: Optional[str] = None


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    title: str
    kind: str
    page: Optional[int] = None
    section: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    knowledge_base_id: Optional[str] = None
    document_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Chunk":
        return cls(**value)


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    lexical_score: float = 0.0
    semantic_score: float = 0.0


@dataclass
class Answer:
    text: str
    sources: List[Chunk]
    refused: bool = False
    backend: str = "extractive"
    backend_error: Optional[str] = None
    source_ids: List[int] = field(default_factory=list)
