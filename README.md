# Aircraft Knowledge Pipeline

Private, source-grounded processing for aircraft documents and Open Knowledge Format output.

The pipeline processes each document version once, preserves its hierarchy and page provenance, and builds reusable evidence for aircraft-specific topics. It does not treat model conversation history as memory.

## Current foundation

Version 0.1 provides:

- SQLite evidence and processing store
- FTS5 exact-text search with BM25 ranking
- Document and revision registry
- Aircraft and operator scopes
- Hierarchical sections and page-aware source chunks
- Canonical topics and aliases
- Positive and negative topic-search records
- Claim-level evidence relationships
- Source-conflict records
- Idempotent processing jobs
- Research-packet and OKF artifact tracking
- Dependency-free Python CLI

Semantic embeddings are intentionally not included yet. They will be added after the evidence contract and exact-search behavior are proven.

## Requirements

- Python 3.12 or newer
- SQLite with FTS5 enabled

Python 3.12 distributions normally include a compatible SQLite build. The test suite verifies FTS5 availability.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Initialize the evidence store

```powershell
akp init-db
akp status
```

The default database is `data/pipeline.db`. Local databases are ignored by Git.

## Register a source document

Source binaries remain local under `sources/` or in other controlled storage.

```powershell
akp register-document `
  "sources/documents/mel/example-mel.pdf" `
  --id "example-b737ng-mel" `
  --type mel `
  --title "Example 737 NG MEL" `
  --authority "Example Operator" `
  --revision "42" `
  --effective-date "2026-04-01" `
  --aircraft "737 NG" `
  --operator "Example Operator" `
  --status current
```

The command calculates the file checksum, registers the document version, and records its scope. It does not commit or copy the source file.

## Register the complete source library

For prototype intake, register every supported file from its folder and filename:

```powershell
akp register-sources
```

The command scans `sources/documents`, derives `document_type` from the first folder, calculates a checksum, and creates one stable document record per relative path. Running it again is safe and does not duplicate unchanged document versions.

To scan a different location:

```powershell
akp register-sources --source-root "D:\controlled-aircraft-sources"
```

## Search indexed evidence

After a future extraction stage has added source chunks:

```powershell
akp search "cargo door" --document-type mel --aircraft "737 NG"
```

Search applies exact full-text ranking after metadata filters. A later hybrid stage will combine these results with semantic retrieval.

## Extract registered PDFs

Extract all registered PDF versions:

```powershell
akp extract-pdfs
```

The extractor:

- Uses PDF bookmarks as hierarchy when the outline is sufficiently detailed
- Falls back to inferred page headings for sparsely bookmarked documents
- Preserves exact PDF page numbers
- Detects printed page labels when present in extracted text
- Splits oversized pages into bounded chunks
- Indexes headings, hierarchy paths, and content with FTS5
- Records a checksum-bound processing job
- Skips successfully extracted unchanged versions on later runs

Extract one document:

```powershell
akp extract-pdfs --document-id "qrh-737-qrh"
```

Rebuild an existing extraction:

```powershell
akp extract-pdfs --document-id "qrh-737-qrh" --force
```

The current extractor handles text-bearing PDFs. OCR for image-only pages and figure extraction are later stages.

## Discover topic candidates

After PDF extraction, discover reviewable topics from the training hierarchy:

```powershell
akp discover-topics
```

The default rule recognizes `Subject <ATA> - <title>` bookmarks in training
documents. It records candidates without silently promoting them to published
topics. Completed discovery is checksum- and processor-version-aware, so an
unchanged rerun is skipped.

Use a different source class, target one document, or intentionally rebuild:

```powershell
akp discover-topics --document-type amm
akp discover-topics --document-id "training-21-029-training"
akp discover-topics --document-id "training-21-029-training" --force
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Repository boundaries

- `sources/`: private local source intake and tracked metadata registry
- `src/`: pipeline storage and retrieval implementation
- `data/`: ignored local database state
- `docs/`: architecture and processing decisions
- `tests/`: deterministic verification

See `docs/ARCHITECTURE.md` for the staged pipeline design and
`docs/FILE_INSTRUCTION_MAP.md` for the canonical run order and file ownership
map.
