import json
import re
import subprocess
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Chunk, Document
from .text import normalize_whitespace, stable_id


SOURCE_URLS = {
    "nist-ai-rmf-1.0.pdf": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
    "nist-genai-profile.pdf": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
    "nist-ai-rmf-playbook.json": "https://airc.nist.gov/docs/playbook.json",
    "nist-ai-rmf-playbook-faq.html": "https://airc.nist.gov/airmf-resources/playbook/faq/",
    "nist-ai-rmf-roadmap.html": "https://www.nist.gov/itl/ai-risk-management-framework/roadmap-nist-artificial-intelligence-risk-management-framework-ai",
}
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm", ".json"}


class ContentHTMLParser(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}
    BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.current: List[str] = []
        self.blocks: List[Tuple[str, str]] = []
        self.tag = ""
        self.title = ""
        self.in_title = False
        self.title_done = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title" and not self.title_done:
            self.in_title = True
        if not self.skip_depth and tag in self.BLOCKS:
            self._flush()
            self.tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            if self.in_title:
                self.title_done = True
            self.in_title = False
        if not self.skip_depth and tag in self.BLOCKS:
            self._flush()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += " " + data
        if not self.skip_depth and not self.in_title:
            value = " ".join(data.split())
            if value:
                self.current.append(value)

    def _flush(self) -> None:
        value = " ".join(self.current).strip()
        if value:
            self.blocks.append((self.tag or "p", value))
        self.current = []


def _pdf_with_pypdf(path: Path) -> Optional[List[str]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    return [(page.extract_text() or "") for page in PdfReader(str(path)).pages]


def _pdf_with_pdfkit(path: Path) -> Optional[List[str]]:
    if sys.platform != "darwin":
        return None
    script = r'''
ObjC.import('Foundation'); ObjC.import('PDFKit');
const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath($.NSProcessInfo.processInfo.environment.objectForKey('NIST_RAG_PDF')));
if (!doc) throw new Error('Cannot open PDF');
for (let i=0; i<doc.pageCount; i++) console.log('__PAGE__' + (i+1) + '\n' + ObjC.unwrap(doc.pageAtIndex(i).string));
'''
    env = dict(__import__("os").environ, NIST_RAG_PDF=str(path.resolve()))
    try:
        run = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], env=env,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    output = run.stdout + "\n" + run.stderr
    parts = re.split(r"__PAGE__\d+\n", output)[1:]
    return [re.sub(r"^\d{4}-.*(?:Connection invalid|Connection Invalid).*\n?", "", p, flags=re.M) for p in parts] or None


def load_pdf(path: Path, retrieved_at: Optional[str] = None) -> List[Document]:
    retrieved_at = retrieved_at or date.today().isoformat()
    pages = _pdf_with_pypdf(path) or _pdf_with_pdfkit(path)
    if pages is None:
        raise RuntimeError("PDF extraction requires `pip install pypdf` (macOS also supports PDFKit)")
    title = path.stem.replace("-", " ").title()
    docs = []
    for number, text in enumerate(pages, 1):
        clean = normalize_whitespace(text)
        if clean:
            docs.append(Document(clean, path.name, title, "pdf", page=number,
                                 source_url=SOURCE_URLS.get(path.name), retrieved_at=retrieved_at))
    return docs


def load_html(path: Path, retrieved_at: Optional[str] = None) -> List[Document]:
    retrieved_at = retrieved_at or date.today().isoformat()
    parser = ContentHTMLParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser._flush()
    title = normalize_whitespace(parser.title) or path.stem
    documents: List[Document] = []
    heading = title
    body: List[str] = []
    for tag, value in parser.blocks:
        if tag.startswith("h"):
            if body:
                documents.append(Document("\n".join(body), path.name, title, "html", section=heading,
                                          source_url=SOURCE_URLS.get(path.name), retrieved_at=retrieved_at))
            heading, body = value, []
        else:
            body.append(("- " if tag == "li" else "") + value)
    if body:
        documents.append(Document("\n".join(body), path.name, title, "html", section=heading,
                                  source_url=SOURCE_URLS.get(path.name), retrieved_at=retrieved_at))
    return documents


def _json_text(value, path: str = "root", depth: int = 0) -> List[str]:
    if depth > 20:
        raise ValueError("JSON nesting exceeds 20 levels")
    if isinstance(value, dict):
        lines: List[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, (dict, list)):
                lines.extend(_json_text(child, child_path, depth + 1))
            elif child not in (None, ""):
                lines.append(f"{child_path}: {child}")
        return lines
    if isinstance(value, list):
        lines = []
        for index, child in enumerate(value):
            lines.extend(_json_text(child, f"{path}[{index}]", depth + 1))
        return lines
    return [f"{path}: {value}"]


def load_json(path: Path, retrieved_at: Optional[str] = None) -> List[Document]:
    retrieved_at = retrieved_at or date.today().isoformat()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else [data]
    docs = []
    for index, record in enumerate(records):
        if isinstance(record, dict):
            section = str(record.get("title") or record.get("id") or f"Record {index + 1}")
            fields = []
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    nested = _json_text(value, key)
                    if nested:
                        fields.append("\n".join(nested))
                elif value not in (None, ""):
                    fields.append(f"{key}: {value}")
            metadata = {key: record.get(key) for key in ("category", "type") if record.get(key) is not None}
        else:
            section = f"Record {index + 1}"
            fields = _json_text(record)
            metadata = {}
        docs.append(Document("\n\n".join(fields), path.name, path.stem.replace("-", " ").title(), "json",
                             section=section, source_url=SOURCE_URLS.get(path.name),
                             retrieved_at=retrieved_at, metadata=metadata))
    return docs


def load_text(path: Path, retrieved_at: Optional[str] = None) -> List[Document]:
    retrieved_at = retrieved_at or date.today().isoformat()
    text = normalize_whitespace(path.read_text(encoding="utf-8"))
    return [Document(text, path.name, path.stem.replace("-", " ").title(), "text",
                     source_url=SOURCE_URLS.get(path.name), retrieved_at=retrieved_at)] if text else []


def load_markdown(path: Path, retrieved_at: Optional[str] = None) -> List[Document]:
    retrieved_at = retrieved_at or date.today().isoformat()
    title = path.stem.replace("-", " ").title()
    documents: List[Document] = []
    heading_path: List[Tuple[int, str]] = []
    body: List[str] = []

    def flush() -> None:
        text = normalize_whitespace("\n".join(body))
        if text:
            section = " > ".join(value for _, value in heading_path) or title
            documents.append(Document(text, path.name, title, "markdown", section=section,
                                      source_url=SOURCE_URLS.get(path.name), retrieved_at=retrieved_at))
        body.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            level, heading = len(match.group(1)), match.group(2)
            heading_path[:] = [(old_level, value) for old_level, value in heading_path if old_level < level]
            heading_path.append((level, heading))
            if level == 1 and title == path.stem.replace("-", " ").title():
                title = heading
        else:
            body.append(line)
    flush()
    return documents


LOADERS = {
    ".pdf": load_pdf,
    ".html": load_html,
    ".htm": load_html,
    ".json": load_json,
    ".md": load_markdown,
    ".markdown": load_markdown,
    ".txt": load_text,
}


def validate_file(path: Path, max_bytes: int = 25 * 1024 * 1024) -> None:
    if path.is_symlink():
        raise ValueError(f"Symbolic links are not accepted: {path.name}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit: {path.name}")
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    extension = path.suffix.lower()
    if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
        raise ValueError(f"File extension does not match PDF content: {path.name}")
    if extension != ".pdf" and b"\x00" in prefix:
        raise ValueError(f"Text document appears to contain binary data: {path.name}")


def discover_files(directory: Path, max_files: int = 20, max_bytes: int = 25 * 1024 * 1024) -> List[Path]:
    root = directory.resolve()
    if not root.is_dir():
        raise ValueError(f"Document directory does not exist: {directory}")
    files = [path for path in sorted(root.rglob("*"))
             if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS]
    if len(files) > max_files:
        raise ValueError(f"Document directory contains {len(files)} supported files; maximum is {max_files}")
    for path in files:
        validate_file(path, max_bytes)
    return files


def load_file(path: Path, source: Optional[str] = None) -> List[Document]:
    loader = LOADERS.get(path.suffix.lower())
    if loader is None:
        raise ValueError(f"Unsupported document type: {path.suffix or '[none]'}")
    documents = loader(path)
    if source:
        for document in documents:
            document.source = source
    return documents


def load_corpus(directory: Path) -> List[Document]:
    root = directory.resolve()
    documents: List[Document] = []
    for path in discover_files(root):
        documents.extend(load_file(path, path.relative_to(root).as_posix()))
    return documents


def _split_long(text: str, max_words: int, overlap: int) -> Iterable[str]:
    words = text.split()
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        yield " ".join(words[start:end])
        if end == len(words):
            break
        start = max(start + 1, end - overlap)


def chunk_documents(documents: Iterable[Document], target_words: int = 450,
                    max_words: int = 650, overlap_words: int = 70) -> List[Chunk]:
    chunks: List[Chunk] = []
    for doc in documents:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", doc.text) if p.strip()]
        groups: List[str] = []
        current: List[str] = []
        count = 0
        for paragraph in paragraphs:
            size = len(paragraph.split())
            if current and count + size > target_words:
                groups.append("\n\n".join(current))
                current, count = [], 0
            current.append(paragraph)
            count += size
        if current:
            groups.append("\n\n".join(current))
        pieces = [piece for group in groups for piece in _split_long(group, max_words, overlap_words)]
        for ordinal, piece in enumerate(pieces):
            chunk_id = stable_id(doc.source, str(doc.page), str(doc.section), str(ordinal), piece)
            chunks.append(Chunk(chunk_id, piece, doc.source, doc.title, doc.kind, doc.page,
                                doc.section, doc.source_url, doc.retrieved_at,
                                dict(doc.metadata, ordinal=ordinal), doc.knowledge_base_id,
                                doc.document_id))
    return chunks
