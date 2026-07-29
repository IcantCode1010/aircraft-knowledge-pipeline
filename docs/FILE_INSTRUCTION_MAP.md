# File Instruction Map

This is the canonical map for how the aircraft knowledge pipeline runs. Update
this file whenever a processing stage, command, durable table, or file ownership
rule changes.

## Operating sequence

| Order | Stage | Command | Reads | Writes | Idempotency record |
|---:|---|---|---|---|---|
| 1 | Initialize store | `akp init-db` | `schema.sql` | `data/pipeline.db` | `schema_metadata` |
| 2 | Register sources | `akp register-sources` | `sources/documents/**` | document, version, and scope rows | document checksum |
| 3 | Extract PDFs | `akp extract-pdfs` | registered PDF versions | sections, chunks, and FTS index | `processing_jobs` / `pdf-extractor-*` |
| 4 | Discover topic candidates | `akp discover-topics` | extracted training hierarchy by default | `topic_candidates` | `processing_jobs` / `topic-discovery-*` |
| 5 | Review and canonicalize topics | not implemented | topic candidates | topics and aliases | future review record |
| 6 | Enrich from MEL/QRH/FCOM/AMM | not implemented | canonical topics and source chunks | searches and evidence | `topic_searches` |
| 7 | Build OKF packets | not implemented | reviewed evidence and claims | OKF Markdown artifacts | `artifacts` |

Run stages in order. A completed processing job is skipped when the source
checksum and processor version have not changed. Use a stage's `--force` option
only when intentionally rebuilding its derived data.

## Source file instructions

| Path | Purpose | Instruction |
|---|---|---|
| `sources/documents/training/` | Default topic seed | Put hierarchical training PDFs here. `Subject <ATA> - <title>` bookmarks become reviewable topic candidates. |
| `sources/documents/amm/` | Maintenance evidence and optional granular topic seed | Put AMM PDFs here. Use `--document-type amm` only when component-level candidates are wanted. |
| `sources/documents/mel/` | Dispatch-relief enrichment | Put MEL/MMEL PDFs here. Absence of a match must be recorded as `not_found`, not treated as an error. |
| `sources/documents/qrh/` | Operational procedure enrichment | Put QRH PDFs here. Only matched procedures should be attached to a topic. |
| `sources/documents/fcom/` | Systems and operational supporting evidence | Put FCOM PDFs here. It is supporting evidence unless explicitly selected as a topic seed. |
| `sources/documents/fim/` | Fault-isolation enrichment | Put FIM PDFs here when available. Missing FIM material does not block a topic. |
| `sources/documents/visual-reference/` | Figures and panels | Put local visual references here. Image processing is not implemented yet. |
| `sources/documents/other/` | Unclassified prototype input | Move a file into a named class before depending on it in automated retrieval. |
| `sources/incoming/` | Temporary intake | Files here are not processed. Classify and move them under `sources/documents/`. |
| `sources/registry/` | Tracked source metadata | Store non-sensitive manifests here; never copy source-document content into the registry. |

Source binaries are private local inputs and must remain ignored by Git.

## Implementation file instructions

| File | Owns | Change when |
|---|---|---|
| `src/aircraft_knowledge_pipeline/schema.sql` | Durable SQLite contract | A stage needs new persistent entities, relationships, constraints, or indexes. |
| `src/aircraft_knowledge_pipeline/store.py` | Database reads/writes and data records | Code needs a new storage operation. Keep SQL out of CLI orchestration. |
| `src/aircraft_knowledge_pipeline/intake.py` | Source discovery, IDs, checksums, and registration | Supported file types or intake naming rules change. |
| `src/aircraft_knowledge_pipeline/pdf_extractor.py` | PDF outline, page text, labels, and chunks | Extraction or page-provenance behavior changes. Bump its processor version. |
| `src/aircraft_knowledge_pipeline/topic_discovery.py` | Candidate rules and discovery jobs | Topic-seed rules change. Bump `TOPIC_DISCOVERY_VERSION`. |
| `src/aircraft_knowledge_pipeline/cli.py` | User commands and JSON summaries | A processing capability needs a command-line entry point. |
| `tests/test_store.py` | Deterministic pipeline contracts | Any behavior above changes. Add a regression test with the change. |
| `docs/ARCHITECTURE.md` | System design and evidence policy | Stage boundaries or provenance rules change. |
| `docs/FILE_INSTRUCTION_MAP.md` | Run order and file ownership | Any stage, command, path, or responsibility changes. |
| `README.md` | Setup and operator quick start | A user-facing command or prerequisite changes. |

## Current topic-discovery rule

The default command is:

```powershell
akp discover-topics
```

It examines extracted `training` documents and recognizes bookmarks shaped like:

```text
Subject 21-25-00 - Recirculation System
```

The ATA code remains in the source hierarchy while `Recirculation System`
becomes a candidate title. Candidates are deliberately stored with status
`candidate`; discovery does not silently publish or canonicalize them.

To inspect another source class:

```powershell
akp discover-topics --document-type amm
```

To target one registered document or rebuild discovery:

```powershell
akp discover-topics --document-id "training-21-029-training"
akp discover-topics --document-id "training-21-029-training" --force
```

## Run tracking and recovery

- `processing_jobs` records the job type, input, checksum-derived hash,
  processor version, status, attempts, error, and timestamps.
- `akp status` reports durable record counts, including topic candidates.
- A normal rerun skips completed unchanged work.
- A failed job can be rerun after correcting the cause.
- A processor behavior change requires a processor-version bump so old results
  are distinguishable from new logic.
- `data/pipeline.db` is derived local state. Back it up if review decisions have
  been added; it is ignored by Git.

## Review gates

1. Topic candidates require human review before becoming canonical topics.
2. A missing MEL, QRH, FIM, or other optional match is valid and must be
   recorded.
3. Evidence must retain its document version, hierarchy, PDF page, and printed
   page.
4. Only reviewed evidence should reach a public OKF artifact.
