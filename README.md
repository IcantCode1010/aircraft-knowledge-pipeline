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

## Promote and enrich topics

Promote selected candidates into stable canonical topics. For the prototype,
the complete eligible queue can be accepted explicitly:

```powershell
akp promote-topics --all --aircraft "737 NG"
```

Promotion preserves the candidate link, creates the canonical title and slug,
adds the source title as an exact-search alias, and carries forward the ATA
chapter. Canonical topics and all retrieved evidence remain `needs_review`.

The condensed default enriches canonical topics from Training and AMM:

```powershell
akp enrich-topics
```

MEL and QRH are optional and must be requested explicitly when a topic needs
dispatch or operational enrichment:

```powershell
akp enrich-topics --topic-id "737ng-recirculation-system" `
  --document-type mel --document-type qrh
```

FCOM is excluded by default because it commonly duplicates Training content.
It remains available through an explicit `--document-type fcom` override.

## Review retrieved evidence

Score and classify the retrieved evidence without approving it:

```powershell
akp triage-evidence
akp evidence-queue --limit 25
```

The queue distinguishes procedure candidates, supporting references, incidental
matches, and ambiguous records requiring manual review. Every item includes its
topic, document class, hierarchy, PDF and printed page, excerpt, score, and
interpretable scoring reasons.

Approve or reject one queue item explicitly:

```powershell
akp review-evidence `
  --topic-id "<topic-id>" `
  --chunk-id "<chunk-id>" `
  --evidence-role "<role>" `
  --decision approved `
  --reviewer "<name>"
```

Manual decisions survive normal retriage. No evidence is eligible for an OKF
artifact until it has been explicitly approved.

## Build a research packet

Create a condensed private Markdown packet containing only explicitly approved
Training and AMM evidence:

```powershell
akp build-research-packet --topic-id "737ng-recirculation-system"
```

The exporter fails if the topic has no approved evidence. Generated packets are
stored under ignored `output/research-packets/` and registered in the local
artifact table with a content hash. The packet is an evidence handoff for
technical synthesis, not an operational instruction or publishable OKF page.

An explicit audit/debug override can include every approved source:

```powershell
akp build-research-packet `
  --topic-id "737ng-recirculation-system" `
  --source-profile all-approved
```

## Draft and review the OKF content form

Add concise claims only after their Training or AMM evidence has been approved:

```powershell
akp add-content-claim `
  --topic-id "737ng-recirculation-system" `
  --section overview `
  --text "The recirculation system combines cabin air with pack air." `
  --chunk-id "<approved-core-chunk-id>" `
  --sort-order 10
```

Build the Markdown content and structure preview:

```powershell
akp build-okf-preview --topic-id "737ng-recirculation-system"
```

The preview separates overview, system flow, components, control logic,
maintenance context, and applicability. Every statement maps to approved core
evidence and remains `needs_review` until explicitly decided:

```powershell
akp review-content-claim `
  --claim-id "<claim-id>" `
  --decision approved `
  --reviewer "<name>"
```

Generated previews are written under ignored `output/okf-previews/`. They are
the content-form approval gate before any TypeScript page implementation.

Record content-model approval and create the separate editorial draft:

```powershell
akp review-content-model `
  --topic-id "737ng-recirculation-system" `
  --decision approved `
  --reviewer "<name>"

akp build-editorial-draft --topic-id "737ng-recirculation-system"
```

Approval is attached to the exact content-model artifact hash. If the model
changes, the new hash requires a new approval. Editorial drafts are written
under ignored `output/editorial-drafts/` with `editorial_status: needs_review`.
Maintenance sections are training summaries: they may identify access,
preparation, test intent, and restoration at a high level, but must not copy
step sequences, switch tables, limits, or task execution details.

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
map. The approved training-diagram language and visual review gates are defined
in `docs/VISUAL_DIRECTION.md`.
