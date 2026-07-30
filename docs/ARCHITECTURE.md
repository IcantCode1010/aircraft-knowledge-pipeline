# Pipeline Architecture

## Objective

Create source-grounded, aircraft-specific OKF articles from hierarchical technical documents without repeatedly sending complete PDFs to a model.

The pipeline follows one rule:

```text
Code narrows and records the evidence.
Models interpret bounded evidence.
Reviewers approve the result.
```

## Processing model

```text
Register document
  -> hash version
  -> extract pages
  -> detect hierarchy
  -> create page-aware chunks
  -> index exact text
  -> discover topic candidates
  -> canonicalize topics and aliases
  -> search optional supporting sources
  -> collect evidence
  -> create research packet
  -> draft evidence-linked claims
  -> assemble OKF content preview
  -> approve content form
  -> assemble editorial draft
  -> approve reader-facing copy
  -> publish
```

Every stage must be:

- Idempotent
- Resumable
- Versioned
- Source-traceable
- Safe to rerun

## Storage layers

### Controlled source library

Source PDFs remain local or in approved private storage. Git tracks the registry and processing artifacts, not licensed document binaries.

### Relational evidence store

SQLite is the first operational store. It records:

- Stable document identity
- Document versions and checksums
- Aircraft and operator scope
- Hierarchical sections
- Page-aware chunks
- Topics and aliases
- Search outcomes
- Evidence and claims
- Conflicts
- Processing jobs
- Generated artifacts

### Exact-text index

SQLite FTS5 indexes headings, hierarchy paths, and source content.

The index is necessary for exact technical identifiers such as:

- ATA references
- NNC identifiers
- MEL item numbers
- Fault codes
- Panel names
- Annunciator text
- Part and task identifiers

Search uses BM25 ranking with headings weighted more strongly than body text.

### Semantic index

Embeddings are a planned second retrieval channel, not the source of truth.

When added, semantic search will:

- Reuse the same source chunk IDs
- Store the embedding model and version
- Cache by chunk content hash
- Apply aircraft, operator, revision, and document-type filters
- Merge results with FTS5 retrieval
- Preserve exact page provenance

The semantic index may initially use a local implementation. PostgreSQL with pgvector is the preferred scale-up path when the pipeline needs multi-user processing or larger retrieval workloads.

## Document-driven discovery

A hierarchical document is traversed once per version. The traversal produces sections, chunks, and topic candidates.

```text
Document
  Chapter
    Section
      Subject
        Procedure or description
```

Each child retains its hierarchy path. A heading such as `Warning System` is therefore stored with context:

```text
ATA 52 > Cargo Doors > Door Warning System
```

The model should process bounded sections or pages. It should not reread the entire source document for every topic.

## Topic-driven enrichment

After a canonical topic exists, the pipeline searches optional source classes:

- QRH
- MEL
- AMM
- FIM
- FCOM
- Training references
- Visual references

Missing optional material is a normal outcome.

Every search records one of:

```text
found
not_found
not_applicable
ambiguous
not_searched
```

The public article omits unsupported optional sections. The internal search record preserves whether a source was actually checked.

## Retrieval sequence

For each topic and source class:

1. Filter by aircraft.
2. Filter by operator when applicable.
3. Filter by current document version.
4. Filter by document type.
5. Search exact identifiers and aliases.
6. Search semantically when available.
7. Fuse and deduplicate candidates.
8. Rerank a bounded set.
9. Send only the best evidence chunks to the extraction model.
10. Save the outcome and its query fingerprint.

## Persistent memory

The database is the pipeline memory. It answers:

- Has this document version already been processed?
- Which processor version created this output?
- Which topics came from this section?
- Was this MEL searched for this topic?
- Was no relevant MEL item found?
- Which pages support this claim?
- Which articles depend on a superseded source?

Model conversation state is never used as the only record of completed work.

## Incremental processing

Processing jobs are keyed by:

```text
job type
input type
input ID
input hash
processor version
```

The same combination produces one reusable job record. A changed document checksum or processor version creates a new job.

Future extractors should also hash individual sections and chunks. When a document revision changes, the pipeline can then:

1. Compare section and chunk hashes.
2. Reprocess changed content.
3. Identify dependent topics and claims.
4. Mark affected artifacts for review.
5. Leave unrelated topics unchanged.

## Initial schema

| Area | Tables |
|---|---|
| Sources | `documents`, `document_versions`, `document_scopes` |
| Extraction | `document_sections`, `source_chunks`, `source_chunks_fts` |
| Topics | `topics`, `topic_aliases`, `topic_candidates` |
| Retrieval | `topic_searches`, `topic_evidence` |
| Trust | `claims`, `claim_evidence`, `source_conflicts`, `conflict_evidence` |
| Processing | `processing_jobs`, `artifacts` |

## Next implementation stages

1. JSON document registry schema and import command
2. Topic-candidate generation
3. Topic alias and canonicalization workflow
4. Optional semantic embeddings and hybrid result fusion
5. Research-packet exporter

The first topic-candidate implementation uses explicit training-manual
`Subject <ATA> - <title>` hierarchy nodes. This produces a small review queue
before canonical topic creation. The rule, command, input/output tables, and
processor-version requirements are maintained in `FILE_INSTRUCTION_MAP.md`.

Accepted candidates become stable aircraft-scoped topics with exact aliases and
ATA metadata. Enrichment then searches each selected supporting document
version independently. It stores `found` and `not_found` outcomes, and attaches
only the bounded best exact-text matches as reviewable evidence.

Retrieved evidence passes through a separate triage table. Deterministic rules
classify likely procedures, supporting references, incidental matches, and
ambiguous results while retaining reasons and a score. These are queue hints,
not publication decisions: only an explicit reviewer action changes evidence
from `needs_review` to `approved` or `rejected`.

The research-packet exporter reads only approved review records and fails when a
topic has none. Its private Markdown output is an auditable evidence inventory
with page provenance and bounded excerpts.

Structured claims are the next trust boundary. Each claim belongs to a stable
content section and must cite one or more approved Training or AMM chunks. The
OKF preview exporter renders those claims into a human-reviewable Markdown
article with an evidence map. It is explicitly not a public or operational
artifact. Content and structure approval must happen before TypeScript work.

Content-model approval is bound to the exact preview artifact hash. The
editorial exporter refuses to run without that approval, then removes internal
claim-state markers and produces reader-facing Markdown with source footnotes.
The resulting editorial artifact has its own `needs_review` state and must be
approved separately before presentation-layer implementation.

Maintenance content is intentionally bounded between two failure modes. It
should explain what access, preparation, checks, and restoration are broadly
involved for training context, while excluding executable step sequences,
switch tables, limits, and other details that could be mistaken for approved
maintenance instructions.

The condensed source policy makes Training and AMM the core topic inputs.
MEL and QRH are optional, topic-specific enrichments and do not enter the core
packet. FCOM is excluded by default to avoid duplicating Training material, but
can be requested explicitly for audits or coverage gaps. Retrieved evidence is
preserved even when the active packet profile excludes it.
6. Approved-content OKF article assembler and validator
7. Revision impact analysis

## Implemented PDF extraction

The first extractor uses `pypdf` and writes directly into the relational evidence contract.

- Detailed PDF bookmarks become nested `document_sections`.
- Sparse or absent bookmarks fall back to the document root plus inferred page headings.
- Every chunk retains exact PDF page numbers.
- Printed page identifiers are captured when visible in extracted text.
- One page may produce multiple bounded chunks, but chunks never silently cross page boundaries.
- A completed checksum-bound extraction job prevents repeated processing.
- `--force` replaces the prior section and chunk set for the same document version.
- Blank or image-only pages are counted and left for the future OCR and visual-extraction stage.
