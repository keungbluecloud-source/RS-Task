import argparse
import json
import sys
from pathlib import Path

from .generation import citation_label
from .index import HybridIndex
from .knowledge_base import KnowledgeBaseStore
from .pipeline import Pipeline


DEFAULT_INDEX = Path("data/index.json")
DEFAULT_STORE = Path("data/knowledge_bases")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nist-rag", description="Build citation-first RAG knowledge bases from local documents")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest", help="Parse documents and build the local index")
    ingest.add_argument("directory", nargs="?", type=Path, help="Directory containing documents")
    ingest.add_argument("--documents", type=Path, default=Path("documents"))
    ingest.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ingest.add_argument("--name", help="Create or update a named knowledge base")
    ingest.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ask = subparsers.add_parser("ask", help="Retrieve evidence and answer a question")
    ask.add_argument("query")
    ask.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ask.add_argument("--knowledge-base", help="Use a named knowledge base")
    ask.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ask.add_argument("--no-llm", action="store_true", help="Use deterministic extractive answers")
    ask.add_argument("--top-k", type=int, default=6)
    search = subparsers.add_parser("search", help="Inspect retrieved chunks without generation")
    search.add_argument("query")
    search.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    search.add_argument("--knowledge-base", help="Use a named knowledge base")
    search.add_argument("--store", type=Path, default=DEFAULT_STORE)
    search.add_argument("--top-k", type=int, default=6)
    listing = subparsers.add_parser("list", help="List named knowledge bases")
    listing.add_argument("--store", type=Path, default=DEFAULT_STORE)
    status = subparsers.add_parser("status", help="Show knowledge-base ingestion status")
    status.add_argument("--knowledge-base", required=True)
    status.add_argument("--store", type=Path, default=DEFAULT_STORE)
    delete = subparsers.add_parser("delete", help="Delete an application-owned knowledge-base index")
    delete.add_argument("--name", required=True)
    delete.add_argument("--store", type=Path, default=DEFAULT_STORE)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            knowledge_bases = KnowledgeBaseStore(args.store).list()
            if not knowledge_bases:
                print("No knowledge bases found.")
            for item in knowledge_bases:
                print(f"{item['name']}\t{item['status']}\t{item['documents']} documents\t{item['updated_at']}")
            return 0
        if args.command == "status":
            manifest = KnowledgeBaseStore(args.store).status(args.knowledge_base)
            summary = {key: manifest.get(key) for key in
                       ("name", "status", "source_directory", "created_at", "updated_at")}
            summary["documents"] = [
                {"path": path, "status": value.get("status"), "chunks": len(value.get("chunks", [])),
                 "error": value.get("error")}
                for path, value in manifest.get("documents", {}).items()
            ]
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0
        if args.command == "delete":
            KnowledgeBaseStore(args.store).delete(args.name)
            print(f"Deleted knowledge-base index '{args.name}'. Source documents were not modified.")
            return 0
        if args.command == "ingest":
            documents = args.directory or args.documents
            if args.name:
                report = KnowledgeBaseStore(args.store).ingest(args.name, documents)
                print(f"Knowledge base '{report.name}': {report.files} files, {report.chunks} chunks "
                      f"({report.added} added, {report.updated} updated, {report.unchanged} unchanged, "
                      f"{report.removed} removed, {report.failed} failed)")
                return 2 if report.failed else 0
            pipeline = Pipeline.ingest(documents, args.index)
            print(f"Indexed {len(pipeline.index.chunks)} chunks in {args.index}")
            return 0
        pipeline = (Pipeline.open_knowledge_base(args.knowledge_base, args.store)
                    if args.knowledge_base else Pipeline.open(args.index))
        if args.command == "search":
            for number, result in enumerate(pipeline.index.search(args.query, args.top_k), 1):
                print(f"{citation_label(result, number)} score={result.score:.4f}")
                print(result.chunk.text[:350].replace("\n", " ") + "\n")
            return 0
        result = pipeline.ask(args.query, args.top_k, use_llm=not args.no_llm)
        print(f"Backend: {result.backend}")
        if result.backend_error:
            print(f"LLM status: {result.backend_error}", file=sys.stderr)
        print(result.text)
        if result.sources:
            print("\nSources:")
            for number, chunk in zip(result.source_ids, result.sources):
                location = f"p. {chunk.page}" if chunk.page else f"§ {chunk.section}"
                excerpt = " ".join(chunk.text.split())[:220]
                print(f"- [S{number}] {chunk.source}, {location}: “{excerpt}…”")
        return 2 if result.refused else 0
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
