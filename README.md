# Citation-First Document RAG

A citation-first retrieval-augmented generation CLI for arbitrary local document folders. It ingests PDF, Markdown, text, HTML, and JSON, maintains isolated named knowledge bases, performs hybrid retrieval, refuses unsupported questions, and emits page/section citations. The supplied NIST AI RMF corpus is the included evaluation example, not a hardcoded requirement.

## Quick start

Python 3.9+ is required. PDF extraction uses `pypdf` on all platforms; on macOS the built-in PDFKit fallback allows the repository to run without dependencies.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[pdf,dev]'
.venv/bin/nist-rag ingest ./documents --name nist
.venv/bin/nist-rag ask --knowledge-base nist "What are the four AI RMF Core functions?" --no-llm
```

The deterministic `--no-llm` path is useful for testing. See **Connect an LLM** below
to enable fluent generated answers.

## Commands

```bash
nist-rag ingest ./my-documents --name project-docs
nist-rag list
nist-rag status --knowledge-base project-docs
nist-rag search "termination clause" --knowledge-base project-docs --top-k 5
nist-rag ask --knowledge-base project-docs "What are the main risks?" --no-llm
nist-rag delete --name project-docs
python3 -m unittest discover -s tests -v
python3 scripts/evaluate.py
```

Re-running `ingest` hashes every supported file and re-parses only new or changed content. It also removes chunks for files no longer present. The application stores manifests and indexes under `data/knowledge_bases/`; it never copies, modifies, or deletes source documents. `delete` removes only the application-owned knowledge-base index.

The legacy explicit-index workflow remains available for scripts:

```bash
nist-rag ingest ./my-documents --index ./data/custom-index.json
nist-rag ask "What are the main risks?" --index ./data/custom-index.json
```

## Connect an LLM

The retrieval pipeline works without an LLM when `--no-llm` is used. To generate a
fluent grounded answer, configure an API that supports the OpenAI-compatible
`/chat/completions` protocol.

The CLI reads `.env` from the directory where the command is run. If you cloned this
repository, create it from the included template:

```bash
cd /path/to/citation-first-document-rag
cp .env.example .env
```

If the CLI was installed from elsewhere, create a `.env` in the directory where you
plan to run it. The file needs these three variables:

```dotenv
OPENAI_API_KEY=replace_with_your_real_key
OPENAI_MODEL=provider_model_name
OPENAI_BASE_URL=https://provider.example/v1
```

The variable names say `OPENAI` because the application uses that API protocol; the
provider itself can be OpenAI, Gemini, or another compatible service.

### Gemini example

Create a Gemini API key in Google AI Studio, then configure:

```dotenv
OPENAI_API_KEY=replace_with_your_gemini_api_key
OPENAI_MODEL=gemini-3.6-flash
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

Do not add quotes unless the value itself requires them, and do not commit `.env`.
The repository's `.gitignore` already excludes it.

### OpenAI-compatible provider example

Use the model name and base URL documented by the selected provider:

```dotenv
OPENAI_API_KEY=replace_with_your_api_key
OPENAI_MODEL=replace_with_a_supported_model
OPENAI_BASE_URL=https://api.openai.com/v1
```

After creating `.env`, ingest documents and ask without `--no-llm`:

```bash
nist-rag ingest ./documents --name my-docs
nist-rag ask --knowledge-base my-docs \
  "What are the main conclusions?"
```

A successful model call begins with:

```text
Backend: llm
```

If the provider cannot be reached or returns an invalid response, the application
does not discard the retrieval result. It prints a safe error and uses the local
extractive fallback:

```text
LLM status: HTTP 503: Service Unavailable
Backend: extractive-fallback
```

Common status meanings:

- `HTTP 401` or `403`: check the API key and account permissions.
- `HTTP 404`: check `OPENAI_BASE_URL` and `OPENAI_MODEL`.
- `HTTP 429`: the provider's rate or quota limit was reached.
- `HTTP 503`: the provider is temporarily unavailable; retry later or select another
  available model.
- `Network error`: check DNS, internet access, proxy, and firewall settings.
- `missing or invalid citations`: the model ignored the required `[S#]` format; the
  pipeline retries once and then safely falls back.

Existing shell environment variables take precedence over `.env`. This allows
production deployments to inject secrets without creating a file.

Only the question and retrieved evidence chunks are sent to the configured LLM. The
application does not upload the entire source folder, but retrieved excerpts can
contain sensitive document content, so choose the provider accordingly.

## Tests

The dependency-free unit suite covers ingestion, retrieval, citation validation,
knowledge-base isolation, incremental updates, and safe deletion:

```bash
python3 -m unittest discover -s tests -v
```

An optional five-document integration corpus exercises PDF, HTML, and JSON together.
Download it from the recorded official sources, then run its tests:

```bash
./scripts/download_test_documents.sh
python3 -m unittest tests.test_mixed_format_corpus -v
```

The downloaded integration corpus is intentionally excluded from Git; the test skips
cleanly when those files are absent.

## How it works

PDFs retain page numbers; Markdown and HTML retain heading paths; JSON retains record identifiers and field paths. Structure-aware chunks feed two dependency-free retrieval signals: BM25 for exact terminology and identifiers, plus hashed unigram/bigram vectors for semantic-like matching. Reciprocal Rank Fusion merges both rankings. The best evidence is checked for query coverage before generation.

The generator receives stable `[S#]` evidence IDs. Citation IDs are validated; an unavailable or invalid LLM response falls back to deterministic evidence extraction. Unsupported questions return an explicit refusal.

## Optional semantic upgrade

Install `.[semantic]` to make Sentence Transformers available. The current offline index deliberately stays lightweight and reproducible; a production adapter can replace hashed vectors behind `HybridIndex` without changing ingestion, answer generation, or citation validation.

## Corpus and provenance

The included `documents/` directory provides the NIST evaluation corpus. Any local directory containing supported files can instead become a named knowledge base. Relative file paths are retained in citations, including for same-named files in different subdirectories.

The additional mixed-format test corpus is downloaded on demand by
`scripts/download_test_documents.sh` from NIST and the SPDX License List Data
repository. The script validates PDF signatures, HTML responses, and JSON syntax
before placing files in `test-documents/`.

## Limitations

- The no-dependency vector signal is lexical feature hashing, not a learned embedding model.
- Scanned PDFs need OCR before ingestion.
- Symbolic links, archives, executable files, and files over 25 MB are not accepted.
- Evidence sufficiency is a conservative lexical coverage heuristic and needs calibration against more labeled questions.
- Extractive mode optimizes auditability, not prose quality.
- The current JSON index is appropriate for this small corpus, not concurrent or very large workloads.
