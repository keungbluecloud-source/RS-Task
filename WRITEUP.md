# Engineering Write-up

## Summary

This project is a citation-first retrieval-augmented generation (RAG) CLI for local
document folders. A user points the CLI at a directory, assigns it a knowledge-base
name, and asks questions against only that knowledge base. The implementation parses
PDF, Markdown, text, HTML, and JSON; preserves page or section metadata; performs
hybrid retrieval; generates an evidence-constrained answer through an
OpenAI-compatible LLM endpoint; validates citations; and falls back to a deterministic
extractive answer when the model is unavailable or unsafe to trust.

The supplied NIST AI Risk Management Framework corpus is the evaluation corpus, not
a hardcoded application dependency. The same CLI was also exercised against a
five-document corpus containing two PDFs, two HTML pages, and one generic JSON file.

## Architecture

```text
Local document folder
        │
        ▼
Format validation and recursive discovery
        │
        ▼
PDF / Markdown / text / HTML / JSON loaders
        │
        ▼
Structure-aware chunks with page/section metadata
        │
        ├── BM25 lexical ranking
        └── hashed unigram/bigram vector ranking
                    │
                    ▼
          Reciprocal Rank Fusion
                    │
                    ▼
        Evidence-sufficiency check
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   explicit refusal     grounded LLM call
                              │
                              ▼
                    citation validation
                              │
                  ┌───────────┴───────────┐
                  ▼                       ▼
            cited answer       extractive fallback
```

The module boundaries follow the failure modes rather than a framework's preferred
layout:

- `ingestion.py` discovers, validates, parses, and chunks source files.
- `knowledge_base.py` owns manifests, incremental updates, index isolation, and safe
  deletion.
- `index.py` implements the two retrieval signals and rank fusion.
- `generation.py` owns evidence checks, the provider boundary, citation repair, and
  fallback behavior.
- `pipeline.py` exposes the application-level question flow.
- `cli.py` provides ingestion, inspection, retrieval, question, and lifecycle
  commands.

Keeping these stages separate made observed failures attributable. For example, an
empty Markdown document was initially marked ready with zero chunks; that was an
ingestion lifecycle bug, not a retrieval problem. A Gemini answer that omitted
`[S#]` markers was a generation-validation failure, not evidence of poor retrieval.

## Document ingestion and knowledge-base lifecycle

The first design targeted only the supplied NIST filenames. I removed that assumption
and introduced a loader registry keyed by extension. All loaders return the same
`Document` representation, after which chunking and retrieval are format-independent.

Format-specific behavior is intentionally retained where it affects citations:

- PDFs are extracted page by page, and every resulting chunk retains its page number.
- Markdown preserves the heading hierarchy as a section path.
- HTML removes scripts, navigation, headers, footers, and other page furniture while
  retaining heading sections and lists.
- Generic JSON objects and arrays are flattened with field paths. Records with a
  recognizable `title` or `id` retain it as their section.
- Plain text uses paragraph boundaries before length constraints are applied.

Files are discovered recursively. Their relative paths, rather than only their base
names, are used as source IDs, so `policies/current.md` and `archive/current.md` do
not collide.

Each named knowledge base has an application-owned manifest and index. The manifest
stores the resolved source directory, per-file SHA-256 hash, size, status, error, and
cached chunks. Re-running ingestion:

1. hashes the current supported files;
2. reuses chunks for unchanged hashes;
3. parses only new or changed files;
4. removes chunks for files no longer present; and
5. atomically replaces the manifest and index.

This made a repeated ingestion of the six-file NIST directory idempotent: the first
run reported six additions, while the second reported six unchanged documents and no
re-parsing failures. A separate mixed-format run produced 114 chunks from five files.

The application indexes source files in place. It does not copy, modify, or own them.
Deleting a knowledge base removes only its manifest and index. A regression test
specifically verifies that the original source document remains on disk.

## Chunking decisions

Chunking is structure-first, then length-bounded. A PDF page, HTML/Markdown section,
or structured JSON record becomes a natural source unit. Paragraphs within that unit
are grouped toward 450 words, with a hard 650-word maximum and 70-word overlap when a
long unit must be split.

I chose word counts rather than model-specific token counts to keep ingestion
provider-neutral and dependency-free. That is a practical compromise, not an ideal
equivalence: punctuation-heavy JSON and prose can have very different token-to-word
ratios. A production implementation should use the selected embedding/generation
tokenizer and calibrate the target against retrieval results.

Page and section metadata are stored on chunks before retrieval. The LLM therefore
never has to invent a page number. Citation locations are application data, not model
claims.

## Embedding and retrieval choices

Retrieval combines BM25 with a dependency-free hashed vector over unigrams and
bigrams. BM25 is important for framework identifiers such as `GOVERN 1.1`, acronyms,
document names, and exact policy language. Feature hashing supplies a second ranking
that tolerates limited phrase variation without a model download. The top candidates
from both rankings are merged using Reciprocal Rank Fusion (RRF), avoiding the need
to normalize incomparable raw score ranges.

An exact-section boost is applied when a query contains a stored section identifier.
This was added after the Generative AI Profile's incidental references to
`GOVERN 1.1` initially outranked the actual Playbook record.

The hashed vector is not a learned semantic embedding. It cannot reliably connect
unseen synonyms or conceptual paraphrases. I retained it because the repository can
install and test without network access or a large model download, but this is the
largest retrieval-quality compromise in the implementation. The next retrieval
upgrade would replace it with a Sentence Transformer and add a small cross-encoder
reranker over the fused candidate set.

The persisted JSON artifact is also an index snapshot, not a production vector
database. It is transparent and adequate for this corpus, but it does not satisfy the
operational characteristics of Chroma, Qdrant, or pgvector at scale.

## Grounded generation, refusal, and citations

The LLM integration uses the OpenAI-compatible `/chat/completions` protocol through
Python's standard library. It has been exercised with Gemini's compatibility
endpoint, while the provider, model, base URL, and API key remain environment
configuration.

Retrieved chunks receive stable evidence IDs such as `[S1]`. The system prompt says
that:

- only supplied evidence may be used;
- document content is untrusted data, not instructions;
- every factual sentence must end in valid `[S#]` markers; and
- insufficient evidence must produce a fixed refusal signal.

Before generation, the pipeline checks lexical relevance and coverage of informative
query terms. This correctly refuses an out-of-scope paid-leave question against the
NIST corpus. The threshold is intentionally explicit and conservative, but it is
heuristic and corpus-dependent.

After generation, every citation ID must refer to evidence that was actually sent to
the model. If citation validation fails, the model receives one repair request with
the evidence and failed draft. A second failure triggers the extractive fallback.
HTTP, network, malformed-response, and provider errors also fall back safely and are
reported as `Backend: extractive-fallback`; a successful model answer reports
`Backend: llm`.

The final Sources list contains only evidence IDs used in the answer and preserves
their original numbering. If an answer cites `[S1][S3]`, the list shows S1 and S3;
it neither includes unused S2 nor renumbers S3.

Citation-ID validation proves provenance but not semantic entailment. A valid `[S2]`
can still be attached to a claim that the chunk does not fully support. Claim-level
entailment checking is a necessary next step for higher-stakes use.

## Security and failure containment

The CLI treats selected folders and their contents as untrusted input. Current
controls include:

- a 25 MB per-file limit and 20-document knowledge-base limit;
- an extension allowlist;
- PDF signature validation;
- rejection of binary content disguised as text;
- rejection of symbolic links;
- a JSON nesting-depth limit;
- safe knowledge-base names and resolved application-owned deletion targets;
- per-document error isolation, so one malformed file does not fail the batch;
- query length, empty-query, and obvious instruction-override checks; and
- prompt boundaries instructing the model not to follow document-embedded commands.

When GitHub push protection identified a Mapbox token embedded in a downloaded public
NIST HTML page, I removed it from the corpus rather than bypassing the protection.
This reinforced that downloaded public HTML still needs secret and active-content
screening before redistribution.

The current parser controls are not a complete hostile-file sandbox. CPU/memory
limits, subprocess isolation, decompression-bomb detection, antivirus scanning, and
more robust MIME detection would be required before accepting uploads from unknown
internet users.

## Evaluation

The NIST evaluation set began with ten questions and now contains sixteen after
adding three additional out-of-scope refusals plus explicit empty-query,
cross-document, and conflicting-source edge cases:

| Category | Count |
| --- | ---: |
| Single-document facts | 3 |
| Exact identifier | 1 |
| Cross-document synthesis | 3 |
| Document comparison | 1 |
| Multi-hop reasoning | 1 |
| Out of scope | 4 |
| Version handling | 1 |
| Empty-query validation | 1 |
| Conflicting-source handling | 1 |

The recorded deterministic run achieved expected-source Hit@5 on 16/16 questions,
including both required sources for the cross-document and version-handling cases.
All four out-of-scope questions were refused. This result establishes source recall
and basic refusal behavior for this small labeled set; it does not establish that
every generated sentence is correct.

`evals/results.json` is also the committed system answer set: it contains 15 actual
answers (including four explicit refusals) and one empty-query validation result.
Every cited answer records its backend and cited document, page or section, and
supporting excerpt. An audit verifies that the stored citation records exactly match
the inline `[S#]` IDs.
The recorded extractive answers expose quality weaknesses, including awkward PDF
line-break artifacts and selecting related rather than directly responsive sentences.

The automated suite currently contains 31 passing tests. It covers loaders, metadata,
chunk bounds, stable IDs, index persistence, exact-identifier retrieval, refusal,
citation validation, `.env` precedence, incremental updates, failed-document
isolation, knowledge-base isolation, safe deletion, path/name validation, and an
optional five-file PDF/HTML/JSON integration corpus. LLM calls are mocked or omitted,
so the suite is deterministic and does not consume API tokens.

Evaluation limitations:

- sixteen questions are too few to establish broad retrieval quality;
- Hit@5 does not measure ranking quality below the cutoff;
- expected answer points are not yet scored automatically;
- citation support is not judged by an entailment model or human double review; and
- refusal precision/recall has only a small number of labeled negative examples.

## What failed and what changed

Several failures materially changed the implementation:

1. **PDF tooling was unavailable.** The environment lacked `pdftotext` and `pypdf`.
   I added `pypdf` as the portable optional dependency and a macOS PDFKit fallback,
   allowing local development without silently dropping page metadata.
2. **Generic retrieval favored incidental identifiers.** GenAI Profile text mentioning
   `GOVERN 1.1` could rank above the Playbook record. Adding document title/source
   tokens and exact-section boosting corrected the tested case.
3. **A generic out-of-scope question passed the first relevance check.** Common terms
   such as “NIST,” “AI,” and “developers” inflated overlap. The sufficiency check now
   distinguishes low-information terms from query-specific evidence.
4. **Gemini returned prose without valid citation markers.** The model call itself had
   succeeded, but post-generation validation correctly rejected it. A stricter system
   prompt and one citation-repair retry produced a valid cited answer.
5. **A heading-only Markdown file was marked ready with zero chunks.** Zero indexable
   chunks now produce a document-level failure instead of a misleading ready state.
6. **The source list included uncited evidence.** Source IDs are now filtered to those
   actually cited while retaining original numbering.
7. **Downloaded HTML contained a token detected by GitHub.** The value was redacted and
   the original commit amended rather than allowing the secret through push protection.

These cases are more informative than a clean happy-path demo because they exercise
the boundaries between retrieval, generation, citation handling, and operations.

## What I would do with another week

In priority order:

1. Replace feature hashing with a learned embedding model and record model/version in
   the index manifest.
2. Add a cross-encoder reranker over fused candidates and evaluate whether its latency
   produces meaningful MRR and citation-recall gains.
3. Label chunk-level relevance for every evaluation question and sweep retrieval,
   fusion, and refusal thresholds rather than tuning individual examples.
4. Add claim-level citation entailment and choose the most relevant supporting span
   rather than always excerpting the beginning of a chunk.
5. Dehyphenate PDF line-break artifacts more reliably and add table-aware extraction
   plus OCR diagnostics.
6. Add query decomposition and per-subquestion evidence coverage for multi-part
   questions.
7. Record per-stage latency, token usage, provider errors, and retrieval traces in an
   evaluation artifact.
8. Run parsers under explicit time and memory limits for hostile-file containment.

## Known limitations

The current implementation has several deliberate and observed limitations:

- **No learned embedding model.** The vector ranking uses hashed unigrams and
  bigrams. It is reproducible and dependency-free, but weak on synonyms and genuine
  semantic paraphrases.
- **No cross-encoder reranker.** RRF combines lexical and vector ranks, but it does not
  perform query–chunk relevance classification over the final candidates.
- **Not a production vector database.** Chunks and index data are persisted as JSON
  and materialized in process. This is transparent at take-home scale but inefficient
  for large, concurrent workloads.
- **Heuristic refusal calibration.** Evidence sufficiency uses lexical coverage and a
  fixed score threshold calibrated on a small NIST evaluation set. Another document
  domain may require different thresholds.
- **Citation validation is structural.** The validator proves that `[S#]` exists in
  the supplied context, not that every cited source semantically entails its claim.
- **Basic PDF extraction.** Complex tables, multi-column layouts, scans, and unusual
  encodings can produce poor text or require OCR. Extracted prose can retain broken
  line-wrap artifacts.
- **Generic JSON flattening.** Field paths are preserved, but arbitrary nested JSON
  may be divided at boundaries that are syntactically convenient rather than
  semantically ideal.
- **Limited adversarial-file containment.** File signatures, size limits, symbolic
  links, and malformed inputs are handled, but parsers do not yet run in isolated
  workers with strict CPU and memory quotas.
- **Small evaluation set.** Sixteen questions are enough to expose regressions but
  not to establish broad generalization, refusal calibration, or domain-independent
  retrieval quality.
- **Single-process local operation.** There is no concurrent ingestion queue,
  authentication layer, tenant authorization service, or distributed observability.

These limitations are intentionally documented because a working answer path alone
does not demonstrate production readiness.

## Scaling to 10× document volume

At roughly ten times the current corpus size, the in-process BM25 structures and JSON
snapshot remain usable, but startup, full index materialization, and manifest size
grow linearly. I would first stop storing duplicate chunk payloads in both manifest
and index, persist lexical statistics separately, batch embedding work, and measure
memory, ingestion latency, query latency, and index size before changing storage
systems. Incremental hashing and changed-file parsing already avoid reprocessing the
entire source folder, so the first likely bottlenecks are index materialization and
query-time scoring over every chunk.

At 10× volume I would specifically:

- replace the JSON manifest's embedded chunk copies with document metadata and chunk
  references;
- persist the learned embedding model name and version with the index;
- batch document embedding and avoid loading all vectors into Python dictionaries;
- add a cross-encoder only over a bounded fused candidate set;
- collect p50/p95 ingestion and query latency before introducing distributed
  services; and
- retain the current CLI and knowledge-base contract so storage changes do not alter
  the user workflow.

## Scaling to 10,000 documents

At 10,000 documents, I would:

- store dense vectors and metadata in Qdrant or pgvector;
- use a service-backed lexical index for BM25;
- run parsing and embedding through idempotent background jobs keyed by content hash;
- version parser, chunker, and embedding configurations;
- use tombstones and transactional swaps for updates;
- cache query embeddings and common retrieval results;
- enforce authorization filters inside retrieval, before evidence reaches the LLM;
- add observability for ingestion failures, latency, cost, retrieval drift, and
  citation-validation rates; and
- maintain a representative regression set per tenant or document domain.

The CLI can remain a client at that scale, but storage and ingestion would no longer
be local process concerns.

## Time and scope

The work was completed over three calendar days, with approximately 12–15 hours of
active implementation, debugging, testing, and documentation. Active time was not
instrumented minute by minute, so this is an honest range rather than a precise timer
total.

I prioritized a complete, inspectable vertical slice: arbitrary folder ingestion,
incremental knowledge bases, hybrid retrieval, grounded generation, citations,
refusal, fallback behavior, tests, and reproducible CLI usage. The principal scope
tradeoffs were using feature hashing instead of learned embeddings, a JSON index
instead of a vector database, and no browser UI. Those choices kept the implementation
small and locally reproducible, but they are also the first things I would revisit for
production quality.
