# RAG Pipeline Implementation Plan

## 1. Product goal

Build a small but complete retrieval-augmented generation system over the NIST AI
Risk Management Framework corpus. A user asks a natural-language question through
a CLI or minimal API and receives:

- A concise answer grounded only in the supplied documents.
- Citations for each material claim.
- Citations containing the document, page or section, and supporting excerpt.
- An explicit refusal when the corpus does not contain enough evidence.
- A transparent comparison when sources disagree or represent different versions.

End-to-end flow:

```text
PDF / HTML / JSON / Markdown
            ↓
Parse, clean, and preserve structure
            ↓
Section-aware chunking
            ↓
Embeddings + keyword index
            ↓
Dense + BM25 candidate retrieval
            ↓
Reranking
            ↓
Relevance and evidence-sufficiency checks
            ↓
Grounded LLM answer
            ↓
Citation validation and final response
```

## 2. Proposed stack

### Application layer

- Python.
- CLI as the primary interface.
- FastAPI as an optional minimal HTTP interface.

The CLI keeps the deliverable easy to install and run. FastAPI can expose an
interactive API without requiring a polished frontend.

### Storage

Use persistent Chroma for the take-home implementation because the corpus is small,
it runs locally, and it can store embeddings, chunks, and citation metadata. Document
the path to Qdrant, pgvector, or another service-backed store at larger scale.

### Embeddings

Use a local Sentence Transformers embedding model by default:

- Avoids embedding API cost and network dependency.
- Fits the English-language corpus.
- Keeps the embedding model configurable and replaceable.
- Uses the same model for documents and queries.

### Generation model

Define a provider-neutral LLM boundary configured through environment variables.
Support an OpenAI-compatible API or local Ollama without committing credentials.
Tests should mock this boundary rather than call a live model.

## 3. Corpus ingestion

The content corpus consists of two PDFs, two HTML pages, and one structured JSON
Playbook. `documents/SOURCES.md` records provenance and is not treated as an
authoritative NIST knowledge document.

### PDF processing

- Extract text page by page.
- Retain page numbers.
- Remove repeated headers, footers, and broken line wrapping.
- Detect empty or abnormally sparse pages and report extraction warnings.
- Preserve headings and list structure where possible.
- Leave a documented extension point for OCR of scanned pages.

PDF chunk metadata should include filename, title, page, section, chunk ID, source
URL, and publication year.

### HTML processing

- Remove navigation, cookie banners, scripts, and repeated footer content.
- Preserve heading hierarchy, body paragraphs, and lists.
- Treat a FAQ question and its answer as one natural semantic unit.
- Store page title, heading path, URL, and retrieval date.

### JSON processing

The Playbook contains 72 structured subcategories. Treat each subcategory as a
logical record, preserving fields such as:

- Identifier, such as `Govern 1.1`.
- Title and description.
- Suggested actions.
- Transparency and documentation guidance.
- AI actors and topics.
- References.

Split an entry further only when it exceeds the chunk limit, preferably at field or
list boundaries.

## 4. Chunking strategy

Use structure-first chunking with length constraints rather than fixed character
windows.

Initial parameters to evaluate:

- Target: 500–700 tokens.
- Maximum: approximately 900 tokens.
- Overlap: 80–120 tokens.
- Preferred boundaries: headings, paragraphs, lists, FAQ entries, and JSON fields.
- Keep tables intact where practical.
- Never combine different documents into one chunk.
- Generally avoid crossing PDF page boundaries; record a start and end page when it
  is necessary.

Store a `parent_section` reference. After a small chunk is retrieved, adjacent chunks
or other fields from the same structured record may be added within a strict context
budget.

The final parameters must be selected using evaluation results, not intuition alone.

## 5. Retrieval strategy

### Candidate retrieval

Run two complementary retrieval methods:

- Dense semantic retrieval for concepts, paraphrases, and synonyms.
- BM25 lexical retrieval for identifiers, acronyms, exact terms, and document names.

Retrieve roughly 10–15 candidates from each method, deduplicate them, and combine
their rankings with Reciprocal Rank Fusion.

### Reranking

Apply a query–chunk cross-encoder reranker to the merged candidates, then retain the
best 4–6 evidence chunks. This improves precision without paying the reranking cost
over the full corpus.

### Context expansion

When a result lands in the middle of a list or section, optionally add:

- The previous or next chunk from the same section.
- Other fields from the same Playbook subcategory.
- A neighboring PDF page.

Expansion must remain bounded to prevent irrelevant context from overwhelming the
answer.

## 6. Grounded answer generation

Present evidence to the LLM under stable source IDs such as `[S1]` and `[S2]`. Each
evidence item carries its document, page or section, and text.

Generation rules:

- Use only the supplied evidence.
- Cite every material factual claim.
- Never cite a source that was not supplied in the context.
- Refuse when evidence is insufficient.
- Represent disagreements or version differences separately.
- Do not mix the model's background knowledge into the answer.
- Include a formatted source list after the answer.
- Use a low generation temperature for repeatability.

## 7. Refusal and hallucination controls

Prompting alone is insufficient, so add controls before and after generation.

### Before generation

- Reject an empty or whitespace-only query.
- Apply a reasonable maximum query length.
- Detect obvious attempts to override the grounded-answer instructions.
- Check whether the strongest retrieval results exceed a calibrated relevance
  threshold.
- Check that enough independent evidence exists.
- For multi-part questions, check whether the retrieved context covers each part.

Thresholds must be calibrated against the evaluation set.

### After generation

- Confirm that every citation ID exists.
- Confirm that every cited chunk was included in the model context.
- Detect uncited material claims.
- Reject invented pages, sections, or documents.
- Require document plus page or section in formatted citations.

Retry once after a validation failure. If validation still fails, return a conservative
refusal with the most relevant excerpts.

## 8. Required edge cases

Handle at least these four cases explicitly:

1. **Empty query:** return a validation error without invoking retrieval or generation.
2. **Answer absent from corpus:** explain that the documents do not provide enough
   information instead of guessing.
3. **Question spanning documents:** retrieve and cite evidence from both the core AI
   RMF and the Generative AI Profile.
4. **Conflicting or time-sensitive sources:** retain publication and retrieval dates,
   show the claims separately, and distinguish a normative publication from a dynamic
   webpage.

## 9. Evaluation set

Create ten questions before tuning the pipeline:

| Question type | Count | Example |
| --- | ---: | --- |
| Single-document fact | 3 | What are the four AI RMF core functions? |
| Exact identifier | 1 | What actions are suggested under Govern 1.1? |
| Cross-document synthesis | 2 | How do AI RMF functions apply to generative-AI risks? |
| Document comparison | 1 | How do the Framework and Playbook differ? |
| Multi-hop reasoning | 1 | How does risk identification lead to mitigation? |
| Out of scope | 1 | What paid-leave policy does NIST recommend? |
| Conflict/version handling | 1 | How should a later web update be compared with an earlier PDF? |

Each evaluation record contains:

- Question and category.
- Expected answer points.
- Expected documents and pages or sections.
- Retrieved chunks.
- Actual answer and citations.
- Pass/fail result and failure reason.

### Retrieval metrics

- Hit@5.
- Mean Reciprocal Rank.
- Citation recall.
- Cross-document source coverage.

### Answer metrics

- Correctness against expected points.
- Whether citations support their associated claims.
- Presence of uncited claims.
- Correct refusal for out-of-scope questions.
- Honest treatment of source conflicts.

Use automated retrieval/citation measurements plus a transparent manual rubric for
answer quality. Classify failures as parsing, chunking, retrieval, reranking,
generation, or citation failures.

## 10. Automated tests

Test deterministic pipeline behavior rather than subjective LLM quality:

- PDF, HTML, and JSON loaders produce the expected metadata.
- Page and section references survive chunking.
- Chunks stay under the maximum size and respect overlap rules.
- Re-ingesting an unchanged file is idempotent.
- Updating a file replaces its affected chunks.
- Exact Playbook identifiers retrieve the expected record.
- Empty queries are rejected.
- Unrelated questions trigger refusal.
- Invalid citation IDs are rejected.
- A cross-document question retrieves both expected sources.
- LLM calls are mocked so the test suite is deterministic and free to run.

## 11. Planned repository structure

```text
documents/          Original corpus and provenance
src/
  ingestion/        Parsing, cleaning, and chunking
  indexing/         Embeddings, BM25, and vector storage
  retrieval/        Hybrid retrieval, fusion, and reranking
  generation/       Prompting, provider boundary, and refusal
  citations/        Citation formatting and validation
  api/              CLI and optional HTTP API
tests/              Unit and small integration tests
evals/              Questions, expected results, and recorded runs
config/             Non-secret configuration
README.md           Installation and usage
WRITEUP.md          Decisions, tradeoffs, limits, and future work
.env.example        Names of required environment variables
```

## 12. Delivery documentation

### README

Include:

- Purpose and supported formats.
- One installation command.
- One ingestion command.
- One question command.
- Test and evaluation commands.
- Model and credential configuration.
- Example questions and outputs.
- A clear statement that secrets must not be committed.

### WRITEUP

Explain specific engineering decisions rather than listing features:

- Why chunking is structure-aware.
- Why retrieval is hybrid.
- Why reranking is a separate stage.
- Why Chroma is appropriate at this scale.
- How refusal thresholds were calibrated.
- What the evaluation exposed.
- Which decisions were constrained by the 36-hour limit.
- What did not work and why.

## 13. Phase 2: user-provided document-folder RAG

### 13.1 Goal

Evolve the fixed NIST corpus implementation into a general RAG CLI where a user can
point the application at an arbitrary local document folder, build a named knowledge
base, and ask questions grounded only in those documents.

The existing retrieval, generation, refusal, and citation pipeline remains in place.
The main changes are how documents are discovered, how indexes are isolated, and how
source-file changes are tracked throughout the index lifecycle.

User flow:

```text
Create a knowledge base or session
                ↓
Select a local document folder
                ↓
Validate, parse, and chunk
                ↓
Build or incrementally update an isolated index
                ↓
Ask questions about the uploaded documents
                ↓
Hybrid retrieval and grounded generation
                ↓
Answer with filename, page or section, and excerpt
```

### 13.2 CLI-first product decision

The take-home brief says that the candidate receives a folder of documents. It does
not require a browser upload workflow. The first general-purpose version therefore
uses a CLI that accepts a directory path rather than adding a web upload system.

The application is installed once and can be run from any directory. Users do not
need to install or copy the application into the document folder.

Proposed workflow:

```bash
# Ingest an arbitrary folder into a named knowledge base.
nist-rag ingest ./my-documents --name project-docs

# Inspect available knowledge bases.
nist-rag list

# Ask a grounded question.
nist-rag ask --knowledge-base project-docs "What are the main risks?"

# Re-run ingestion after adding, changing, or removing files.
nist-rag ingest ./my-documents --name project-docs

# Remove a knowledge base and its local index.
nist-rag delete --name project-docs
```

For a one-off workflow, an explicit index path remains available:

```bash
nist-rag ingest ./my-documents --index ./data/project-docs.json
nist-rag ask "What are the main risks?" --index ./data/project-docs.json
```

This decision keeps the implementation aligned with the assignment and concentrates
engineering time on parsing, retrieval, citations, refusal behavior, and evaluation.
A FastAPI upload interface remains a documented future extension rather than a
requirement for the take-home deliverable.

### 13.3 Initial scope

The first folder-ingestion version supports:

- PDF.
- Markdown.
- Plain text.
- HTML.
- JSON objects and arrays.
- Multiple documents discovered recursively within one selected folder.
- Independent knowledge bases identified by generated IDs.

The first version does not include:

- Image, audio, or video ingestion.
- Word, Excel, or PowerPoint parsing.
- OCR for scanned PDFs.
- Cloud-drive or remote-URL imports.
- Browser uploads, a complete user-account system, and authentication.

Initial safety and resource limits:

- Maximum file size: 25 MB.
- Maximum documents per knowledge base: 20.
- Validate both file extension and detected content type.
- Reject executables, archives, and unsupported formats.
- Resolve and validate user-provided directory and file paths before reading them.
- Restrict every query to its selected knowledge base.

These values remain configurable and should be adjusted using observed ingestion
time, memory usage, and deployment constraints.

### 13.4 Data model

Introduce explicit knowledge-base and document records:

```text
KnowledgeBase
  id
  name
  created_at
  status

SourceDocument
  id
  knowledge_base_id
  original_filename
  relative_path
  content_hash
  mime_type
  size
  ingestion_status
  error
  created_at

Chunk
  id
  knowledge_base_id
  document_id
  text
  page
  section
  ordinal
  metadata
```

Every stored chunk and retrieval request must carry a `knowledge_base_id`. This is a
hard isolation boundary: content indexed in one knowledge base must never appear in
another knowledge base's search results or LLM context.

### 13.5 Generalized ingestion

Replace the hardcoded NIST source allowlist with a loader registry:

```python
LOADERS = {
    ".pdf": PDFLoader,
    ".md": MarkdownLoader,
    ".txt": TextLoader,
    ".html": HTMLLoader,
    ".json": JSONLoader,
}
```

All loaders produce the same internal `Document` representation with text, title,
source filename, page or section, and format-specific metadata.

Format behavior:

- PDF: extract page by page and retain page numbers.
- Markdown: preserve heading hierarchy and list boundaries.
- Text: split at paragraphs before applying length limits.
- HTML: remove scripts, navigation, and repeated page furniture; preserve headings.
- JSON: handle objects and arrays recursively, preserving field paths and record
  identifiers when possible.

An empty, encrypted, malformed, or otherwise unreadable document should receive a
document-level ingestion error. It must not cause other files in the folder scan to
fail.

### 13.6 CLI knowledge-base storage

Add named knowledge-base management to the existing CLI:

```text
nist-rag ingest DIRECTORY --name NAME
nist-rag list
nist-rag status --knowledge-base NAME
nist-rag ask --knowledge-base NAME QUESTION
nist-rag delete --name NAME
```

Initial local storage layout:

```text
data/
  knowledge_bases/
    {knowledge_base_id}/
      manifest.json
      index.json
```

The manifest records the resolved source-directory path and document fingerprints.
The application indexes files in place and does not copy or take ownership of the
user's originals. Original relative paths and filenames are retained for citations.
Knowledge-base names are converted to safe generated storage identifiers so they
cannot escape the application data directory.

### 13.7 Incremental indexing and lifecycle

Re-running ingestion after a folder changes should not require rebuilding every
document in the knowledge base.

Ingestion sequence:

1. Resolve the selected directory and enumerate supported files safely.
2. Validate each file and calculate its SHA-256 content hash.
3. Detect duplicate content within the knowledge base.
4. Parse and chunk only new or changed documents.
5. Replace chunks previously associated with changed document IDs.
6. Remove chunks for files no longer present in the selected folder.
7. Atomically update the index and manifest.
8. Mark each document ready or record a structured failure.

Document status transitions:

```text
discovered → parsing → chunking → indexing → ready
                                      ↓
                                    failed
```

The first implementation may process small files synchronously. Larger deployments
should move ingestion to a job queue and expose progress through the status endpoint.
Removing a document from the index must remove its manifest entry and all associated
chunks but must never delete the user's original file. Deleting a knowledge base must
remove only its explicitly resolved application-owned manifest and index directory.

### 13.8 Retrieval and answer changes

Reuse the current BM25, vector ranking, Reciprocal Rank Fusion, evidence-sufficiency
checks, Gemini generation, citation validation, repair retry, and extractive fallback.

Required changes:

- Filter all retrieval by `knowledge_base_id`.
- Display the user's original filename in citations.
- Use PDF page numbers when available.
- Use Markdown, HTML, or JSON section paths when page numbers do not exist.
- Never include chunks from another knowledge base in the model context.
- Invalidate citations immediately after their source document is deleted.
- Select a claim-relevant excerpt from each cited chunk instead of always displaying
  the beginning of the chunk.

Example response:

```text
The agreement allows either party to terminate with 30 days' notice [S1].

Sources:
- [S1] vendor-agreement.pdf, p. 12:
  “Either party may terminate this agreement with thirty days' notice...”
```

### 13.9 Future browser interface

A browser interface is explicitly deferred until after the CLI version meets the
retrieval and evaluation goals. A future minimal FastAPI page may provide:

- Knowledge-base creation or selection.
- Drag-and-drop or file-picker upload.
- Per-document status: uploading, processing, ready, or failed.
- A document list with delete controls.
- A question input.
- A grounded answer with clickable or clearly labeled citations.
- Visible indication of whether the answer used the LLM or extractive fallback.

FastAPI can later serve a small HTML, CSS, and JavaScript interface. A separate
frontend framework is unnecessary until the API and workflow stabilize.

### 13.10 File and prompt security

Reading user-selected files adds risks beyond those in the fixed-corpus
implementation. Add controls for:

- Maximum file size and document count.
- Extension and MIME validation.
- Safe knowledge-base storage names.
- Path traversal prevention.
- Parser time and memory limits.
- PDF decompression-bomb and pathological-file handling.
- Maximum JSON nesting depth and record count.
- Removal of executable HTML and scripts.
- Treating document text as untrusted data rather than model instructions.
- Explicit prompt boundaries separating system rules, user questions, and documents.
- Knowledge-base authorization and isolation before retrieval.
- Safe, explicitly resolved deletion targets.

Content such as “ignore previous instructions” inside an uploaded document must be
retrievable as document content but must never override the grounded-answer system
instructions.

### 13.11 Folder-ingestion test plan

Add deterministic tests for:

- Ingesting valid PDF, Markdown, text, HTML, and JSON documents from a selected folder.
- Rejecting unsupported and oversized files.
- Sanitizing filenames such as `../../secret.pdf`.
- Preventing duplicate chunks after repeated uploads.
- Handling same-named files in different subdirectories.
- Isolating a failed file from the rest of an upload batch.
- Replacing chunks after a document update.
- Removing all associated chunks after document deletion.
- Preventing knowledge base A from retrieving knowledge base B's content.
- Retaining PDF pages and Markdown or HTML headings in citations.
- Refusing questions not answered by the selected knowledge base.
- Treating prompt-injection text inside documents as untrusted content.
- Repairing or rejecting invented citation IDs.
- Returning only sources actually cited in the answer.

### 13.12 Implementation order

Implement the folder-ingestion phase in this order:

1. Remove the NIST-specific source allowlist and introduce the loader registry.
2. Add knowledge-base, source-document, and chunk ownership models.
3. Implement safe directory discovery, named knowledge-base storage, and manifests.
4. Add incremental indexing and document deletion.
5. Enforce knowledge-base filtering throughout retrieval.
6. Add `list`, `status`, and `delete` CLI commands.
7. Add file validation, parser limits, and prompt-injection boundaries.
8. Add isolation, lifecycle, security, and citation tests.
9. Update the README, WRITEUP, architecture diagram, and evaluation set.
10. Document FastAPI uploads and a browser interface as optional future work.

The implementation should preserve the currently working citation-first pipeline and
replace only the fixed-corpus assumptions. This avoids rewriting retrieval and
generation while making document ownership and isolation explicit.

Document known limitations honestly:

- Basic PDF extraction is weak on scans, tables, and complex layouts.
- Ten evaluation questions do not establish broad generalization.
- Relevance thresholds are corpus-dependent.
- Citation validation does not fully prove semantic entailment.
- Local models may trail larger hosted models in answer quality.
- The local storage and index update design is not intended for high-volume ingestion.

Record approximate time spent, as requested by the assignment.

## 14. Scaling to 10,000 documents

The follow-up design should cover:

- Asynchronous ingestion through a task queue.
- Object storage for original documents.
- File hashing, deduplication, and incremental re-indexing.
- Batched and cached embeddings.
- A service-backed vector database.
- OpenSearch or Elasticsearch for lexical retrieval.
- Metadata filtering for organization, permissions, type, and date.
- Tenant isolation and access control.
- Offline evaluation, user feedback, and retrieval telemetry.
- Monitoring of low-confidence answers and citation mismatches.
- Rebuilding only chunks affected by a document update.

## 15. Proposed 36-hour schedule

### Phase 1 — Corpus and evaluation design: 3 hours

- Inspect all five documents.
- Define the metadata schema.
- Write ten evaluation questions and expected citations first.
- Establish success criteria.

### Phase 2 — Ingestion: 6 hours

- Implement all three content parsers.
- Clean and chunk documents.
- Produce an ingestion report.
- Verify pages, sections, warnings, and chunk counts.

### Phase 3 — Retrieval: 6 hours

- Build dense and BM25 indexes.
- Add rank fusion and reranking.
- Run retrieval evaluation.
- Tune chunk size, top-k, and fusion parameters from measured results.

### Phase 4 — Answers and citations: 5 hours

- Create the grounded prompt.
- Produce structured answers and citations.
- Add refusal logic and citation validation.

### Phase 5 — CLI/API and tests: 5 hours

- Build a clear command interface.
- Complete ingestion, retrieval, and edge-case tests.
- Verify a clean-environment workflow.

### Phase 6 — Evaluation and error analysis: 5 hours

- Run all ten questions.
- Save results and metrics.
- Categorize and investigate failures.

### Phase 7 — Documentation and final verification: 6 hours

- Complete README and WRITEUP.
- Check configuration and secret handling.
- Run installation, ingestion, query, tests, and evaluation from scratch.
- Prepare a two-minute architecture walkthrough and tradeoff discussion.

## 16. Definition of done

The project is complete when:

- All five content documents ingest successfully with traceable metadata.
- A user can ask questions through a documented CLI or API.
- Answers are grounded and carry valid document/section citations.
- Missing-answer, empty-query, cross-document, and conflict cases are handled.
- Ten evaluation questions and their recorded results are committed.
- Retrieval and ingestion tests pass without a live LLM call.
- README provides a clean one-command path to install and run.
- WRITEUP documents real decisions, evidence, limitations, scale-up changes, and time
  spent.
