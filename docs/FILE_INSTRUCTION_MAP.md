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
| 5 | Review and canonicalize topics | `akp promote-topics` | selected topic candidates | topics, aliases, and accepted candidate links | candidate status |
| 6 | Enrich core topic | `akp enrich-topics` | canonical topics plus Training and AMM chunks | searches and evidence | `topic_searches` / `topic-enrichment-*` |
| 7 | Triage and review evidence | `akp triage-evidence`, `akp evidence-queue`, `akp review-evidence` | retrieved topic evidence | scored review records and human decisions | `evidence_reviews` / `evidence-review-*` |
| 8 | Build research packet | `akp build-research-packet` | approved evidence only | private Markdown evidence packet | `artifacts` / `akp-research-packet/v2` |
| 9 | Draft OKF claims | `akp add-content-claim` | approved Training/AMM evidence | sectioned claims and claim-evidence links | stable claim ID |
| 10 | Build OKF preview | `akp build-okf-preview` | structured evidence-linked claims | reviewable Markdown content form | `artifacts` / `akp-okf-preview/v1` |
| 11 | Approve content model | `akp review-content-model` | exact OKF preview artifact | approved/rejected model state | `artifact_reviews` |
| 12 | Build editorial draft | `akp build-editorial-draft` | approved content model and linked claims | reader-facing Markdown | `artifacts` / `akp-editorial-draft/v2` |
| 13 | Review claims/copy | `akp review-content-claim` | editorial draft and source evidence | approved/rejected claim states | claim review metadata |
| 14 | Implement content page | not implemented | approved editorial copy | TypeScript page | future OKF artifact |

Run stages in order. A completed processing job is skipped when the source
checksum and processor version have not changed. Use a stage's `--force` option
only when intentionally rebuilding its derived data.

## Source file instructions

| Path | Purpose | Instruction |
|---|---|---|
| `sources/documents/training/` | Core topic seed and explanation | Put hierarchical training PDFs here. `Subject <ATA> - <title>` bookmarks become candidates and approved pages feed the core packet. |
| `sources/documents/amm/` | Core maintenance evidence | Put AMM PDFs here. Approved AMM pages feed the core packet. |
| `sources/documents/mel/` | Optional dispatch enrichment | Request explicitly for a topic. Keep only direct item matches; absence is valid. |
| `sources/documents/qrh/` | Optional operational enrichment | Request explicitly for a topic. Keep only directly related procedures. |
| `sources/documents/fcom/` | Explicit override only | Excluded from the default process because it commonly overlaps Training. |
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
| `src/aircraft_knowledge_pipeline/okf_preview.py` | Evidence-linked Markdown content form | Article sections, evidence map, or content approval contract changes. |
| `src/aircraft_knowledge_pipeline/editorial_draft.py` | Reader-facing Markdown and source footnotes | Editorial form or post-model approval behavior changes. |
| `docs/VISUAL_DIRECTION.md` | Approved visual language and diagram review gates | Visual style, source-grounding rules, or diagram approval boundaries change. |
| `src/aircraft_knowledge_pipeline/topic_discovery.py` | Candidate rules and discovery jobs | Topic-seed rules change. Bump `TOPIC_DISCOVERY_VERSION`. |
| `src/aircraft_knowledge_pipeline/topic_canonicalization.py` | Candidate promotion, stable topic IDs, aliases, and ATA metadata | Canonical naming or promotion rules change. |
| `src/aircraft_knowledge_pipeline/topic_enrichment.py` | Per-topic supporting-document search and evidence linking | Retrieval logic changes. Bump `TOPIC_ENRICHMENT_VERSION`. |
| `src/aircraft_knowledge_pipeline/evidence_review.py` | Evidence scoring, classification, and review-queue rendering | Triage heuristics change. Bump `EVIDENCE_REVIEW_VERSION`. |
| `src/aircraft_knowledge_pipeline/research_packet.py` | Approved-evidence packet generation and artifact registration | Packet structure changes. Bump `RESEARCH_PACKET_SCHEMA`. |
| `src/aircraft_knowledge_pipeline/source_policy.py` | Core, optional, and excluded-by-default source classes | Any default source policy changes. |
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

## Canonicalization and enrichment

Promotion is an explicit review action. Promote named candidates with repeated
`--candidate-id` arguments, or deliberately accept the complete eligible
prototype queue:

```powershell
akp promote-topics --candidate-id "<candidate-uuid>"
akp promote-topics --all --aircraft "737 NG"
```

Promotion creates stable topic IDs, records the source title as an alias,
extracts the ATA chapter from the hierarchy, and links each accepted candidate
to its canonical topic. New topics remain `needs_review` until their content is
approved for publication.

Add a search synonym when a source uses different terminology:

```powershell
akp add-topic-alias `
  --topic-id "737ng-recirculation-system" `
  --alias "Recirculation Fan"
```

Search the core Training and AMM sources:

```powershell
akp enrich-topics
```

Limit the run to a topic, or request optional MEL and QRH enrichment:

```powershell
akp enrich-topics --topic-id "737ng-recirculation-system"
akp enrich-topics --document-type mel --document-type qrh
```

FCOM requires `--document-type fcom`. Each requested topic/document-version pair
produces a durable `found` or `not_found` record. Found chunks are linked as
evidence with `needs_review` status.

## Evidence review

Create or refresh scored review records for retrieved evidence:

```powershell
akp triage-evidence
```

Triage classifies evidence as `procedure_candidate`, `supporting_reference`,
`incidental`, or `manual_review`. It records interpretable reasons and never
approves or rejects evidence automatically.

Inspect the queue, optionally filtering it:

```powershell
akp evidence-queue --limit 25
akp evidence-queue --classification procedure_candidate
akp evidence-queue --classification incidental
```

Record a human decision using the composite identity shown in the queue:

```powershell
akp review-evidence `
  --topic-id "737ng-recirculation-system" `
  --chunk-id "<chunk-uuid>" `
  --evidence-role "mel_support" `
  --decision approved `
  --reviewer "reviewer-name"
```

Normal retriage preserves approved and rejected decisions. `--force` explicitly
resets matching review records to `needs_review`, so use it only after a scoring
rule change or when a fresh human review is intended.

## Research packets

Build the condensed core packet after at least one Training or AMM item has been
explicitly approved:

```powershell
akp build-research-packet --topic-id "737ng-recirculation-system"
```

The `core` profile is the default and includes only Training and AMM. The
`all-approved` profile is an explicit audit override:

```powershell
akp build-research-packet `
  --topic-id "737ng-recirculation-system" `
  --source-profile all-approved
```

Packets contain source metadata, hierarchy, page references, review metadata,
and bounded excerpts. They are written under ignored
`output/research-packets/`. Excluded evidence remains in the database.

## OKF enrichment preview

Draft claims into the canonical content sections only after their core evidence
has been approved:

```powershell
akp add-content-claim `
  --topic-id "737ng-recirculation-system" `
  --section overview `
  --text "<concise source-grounded statement>" `
  --chunk-id "<approved-training-or-amm-chunk-id>"
```

Valid sections are `overview`, `system_flow`, `components`, `control_logic`,
`maintenance_context`, and `applicability`. A claim may cite multiple chunks.
Claims backed by unapproved evidence, optional sources, or unknown chunks are
rejected.

Build the content-form preview:

```powershell
akp build-okf-preview --topic-id "737ng-recirculation-system"
```

Review claim wording separately from the page structure:

```powershell
akp review-content-claim `
  --claim-id "<claim-id>" `
  --decision approved `
  --reviewer "<reviewer-name>"
```

The preview remains non-operational and non-publishable until all claims and
the structure are approved. TypeScript implementation starts only after this
gate.

Record the content owner's decision against the exact preview artifact:

```powershell
akp review-content-model `
  --topic-id "737ng-recirculation-system" `
  --decision approved `
  --reviewer "<reviewer-name>"
```

Then create the independently reviewable editorial version:

```powershell
akp build-editorial-draft --topic-id "737ng-recirculation-system"
```

The editorial exporter fails without an approved content-model artifact. A
content-model rebuild with unchanged content preserves approval; changed
content creates a new hash that requires fresh approval.

## Run tracking and recovery

- `processing_jobs` records the job type, input, checksum-derived hash,
  processor version, status, attempts, error, and timestamps.
- `evidence_reviews` records classification, score, reasons, processor version,
  review state, reviewer, and review timestamp.
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
4. Only evidence with an explicit `approved` decision may reach a public OKF
   artifact.
5. The core research packet contains Training and AMM only. Optional MEL/QRH
   evidence is handled separately and FCOM requires an explicit override.
