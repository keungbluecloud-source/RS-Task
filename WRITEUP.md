# Engineering write-up

## Architecture and decisions

The pipeline separates parsing, chunking, indexing, retrieval, generation, and citation validation. The boundary matters because each failure can be diagnosed independently: missing evidence can be traced to extraction, chunking, retrieval, or generation rather than treated as a generic “RAG quality” issue.

The primary interface accepts an arbitrary local folder and stores it as a named knowledge base. This matches the take-home's folder-based input more directly than building a browser upload product. A manifest records relative paths, hashes, statuses, and cached chunks; repeated ingestion parses only changed files and removes stale chunks. Each knowledge base has a separate index. Deleting it removes application data only, never the source folder.

Chunking follows source structure first. A PDF page, HTML heading section, or Playbook subcategory is never silently mixed with another source unit. Text is then grouped near 450 words with a hard 650-word ceiling. Page and section metadata live on every chunk, so citations do not depend on the model remembering locations.

Retrieval combines BM25 and a lightweight hashed unigram/bigram vector with Reciprocal Rank Fusion. BM25 is valuable for `GOVERN 1.1`, acronyms, and exact framework language. The second ranking tolerates some phrase variation and supplies a dependency-free baseline. A learned Sentence Transformer would improve paraphrase recall, but requiring a large first-run download makes the take-home less reproducible. The retrieval boundary makes that upgrade local.

Generation is provider-neutral through an OpenAI-compatible HTTP boundary. Evidence is labeled before it reaches the model. Unsupported queries are stopped by evidence coverage checks, and returned citation IDs are validated. If no credential exists or the response invents an ID, the system returns a deterministic extractive answer rather than silently trusting it.

## Evaluation and failure analysis

The ten-question set is committed before tuning and covers facts, exact identifiers, synthesis, comparison, multi-hop reasoning, refusal, and version handling. `scripts/evaluate.py` records top-five sources and deterministic answers. Failures should be classified as parsing, chunking, retrieval, generation, or citation failures; source hit rate can therefore improve separately from prose quality.

The initial thresholds are intentionally conservative. They are explicit values in code, not presented as universally correct. A proper tuning pass would label relevant chunks for every question, sweep candidate counts and evidence thresholds, and report Hit@5, MRR, citation recall, refusal precision, and source coverage.

## Known limitations and another week

I would first add learned embeddings and a small cross-encoder reranker, then label chunk-level relevance for the evaluation set and calibrate refusal. Next I would add claim-level citation entailment, multi-part query decomposition, adjacency expansion, and OCR diagnostics. I would also record latency and token cost per stage and add fixtures covering malformed PDFs and changing web markup.

At ten times the current volume, the in-process index remains workable but rebuild cost grows. At 10,000 documents I would move dense vectors to Qdrant or pgvector, lexical search to a service-backed index, perform incremental ingestion keyed by content hash, queue embedding jobs, and version both chunks and embedding models. Access control would be enforced during retrieval, before evidence reaches the model.

## Time and scope

This implementation favors a complete, inspectable vertical slice over a framework-heavy demo. The main deliberate compromise is the lightweight vector fallback. It keeps installation and tests reliable while making the quality tradeoff visible and replaceable.
