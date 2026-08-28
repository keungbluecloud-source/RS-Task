import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .index import HybridIndex
from .ingestion import chunk_documents, discover_files, load_file
from .models import Chunk
from .text import stable_id


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class IngestionReport:
    name: str
    files: int
    chunks: int
    added: int
    updated: int
    unchanged: int
    removed: int
    failed: int


class KnowledgeBaseStore:
    def __init__(self, root: Path = Path("data/knowledge_bases")) -> None:
        self.root = root.resolve()

    def _validate_name(self, name: str) -> str:
        if not NAME_RE.fullmatch(name) or name in {".", ".."} or ".." in name:
            raise ValueError("Knowledge-base name must use 1-64 letters, numbers, dots, underscores, or hyphens")
        return name

    def directory(self, name: str) -> Path:
        name = self._validate_name(name)
        target = (self.root / name).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid knowledge-base path")
        return target

    def manifest_path(self, name: str) -> Path:
        return self.directory(name) / "manifest.json"

    def index_path(self, name: str) -> Path:
        return self.directory(name) / "index.json"

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read_manifest(self, name: str) -> Optional[Dict]:
        path = self.manifest_path(name)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
    def _atomic_json(path: Path, payload: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def ingest(self, name: str, source_directory: Path) -> IngestionReport:
        self._validate_name(name)
        source_root = source_directory.resolve()
        files = discover_files(source_root)
        existing = self._read_manifest(name)
        old_documents = existing.get("documents", {}) if existing else {}
        current_paths = {path.relative_to(source_root).as_posix(): path for path in files}
        new_documents: Dict[str, Dict] = {}
        added = updated = unchanged = failed = 0

        for relative_path, path in current_paths.items():
            fingerprint = self._hash(path)
            previous = old_documents.get(relative_path)
            if previous and previous.get("sha256") == fingerprint and previous.get("status") == "ready":
                new_documents[relative_path] = previous
                unchanged += 1
                continue
            document_id = previous.get("id") if previous else stable_id(name, relative_path)
            try:
                documents = load_file(path, relative_path)
                for document in documents:
                    document.knowledge_base_id = name
                    document.document_id = document_id
                chunks = chunk_documents(documents)
                if not chunks:
                    raise ValueError("Document contains no indexable text")
                new_documents[relative_path] = {
                    "id": document_id,
                    "relative_path": relative_path,
                    "sha256": fingerprint,
                    "size": path.stat().st_size,
                    "status": "ready",
                    "error": None,
                    "chunks": [chunk.to_dict() for chunk in chunks],
                }
                if previous:
                    updated += 1
                else:
                    added += 1
            except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                new_documents[relative_path] = {
                    "id": document_id,
                    "relative_path": relative_path,
                    "sha256": fingerprint,
                    "size": path.stat().st_size,
                    "status": "failed",
                    "error": str(error)[:500],
                    "chunks": [],
                }
                failed += 1

        removed = len(set(old_documents) - set(current_paths))
        chunks = [Chunk.from_dict(value) for document in new_documents.values()
                  for value in document.get("chunks", [])]
        now = self._now()
        manifest = {
            "version": 1,
            "name": name,
            "source_directory": str(source_root),
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
            "status": "ready" if not failed else "ready_with_errors",
            "documents": new_documents,
        }
        target = self.directory(name)
        target.mkdir(parents=True, exist_ok=True)
        HybridIndex(chunks).save(self.index_path(name))
        self._atomic_json(self.manifest_path(name), manifest)
        return IngestionReport(name, len(files), len(chunks), added, updated, unchanged, removed, failed)

    def list(self) -> List[Dict]:
        if not self.root.is_dir():
            return []
        values = []
        for path in sorted(self.root.iterdir()):
            manifest = path / "manifest.json"
            if path.is_dir() and manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    values.append({"name": data["name"], "status": data.get("status", "unknown"),
                                   "documents": len(data.get("documents", {})),
                                   "updated_at": data.get("updated_at")})
                except (OSError, ValueError, KeyError):
                    continue
        return values

    def status(self, name: str) -> Dict:
        manifest = self._read_manifest(name)
        if manifest is None:
            raise FileNotFoundError(f"Knowledge base not found: {name}")
        return manifest

    def delete(self, name: str) -> None:
        target = self.directory(name)
        if not (target / "manifest.json").is_file():
            raise FileNotFoundError(f"Knowledge base not found: {name}")
        shutil.rmtree(target)
