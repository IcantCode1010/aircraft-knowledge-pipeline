from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .intake import register_source_tree
from .pdf_extractor import extract_registered_pdfs
from .store import KnowledgeStore, sha256_file
from .topic_discovery import (
    DEFAULT_TOPIC_SOURCE_TYPES,
    discover_topic_candidates,
)
from .topic_canonicalization import promote_topic_candidates
from .topic_enrichment import DEFAULT_ENRICHMENT_TYPES, enrich_topics
from .evidence_review import render_review_rows, triage_evidence
from .editorial_draft import (
    DEFAULT_EDITORIAL_DRAFT_ROOT,
    build_editorial_draft,
)
from .okf_preview import DEFAULT_OKF_PREVIEW_ROOT, build_okf_preview
from .research_packet import DEFAULT_PACKET_ROOT, build_research_packet
from .source_policy import SOURCE_PROFILES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="akp",
        description="Aircraft knowledge pipeline storage and retrieval CLI.",
    )
    parser.add_argument(
        "--db",
        default="data/pipeline.db",
        help="SQLite database path. Default: data/pipeline.db",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="Create or upgrade the local evidence database.")
    commands.add_parser("status", help="Show evidence-store record counts.")

    register_sources = commands.add_parser(
        "register-sources",
        help="Register every supported file under a source directory.",
    )
    register_sources.add_argument(
        "--source-root",
        default="sources/documents",
        help="Document tree to register. Default: sources/documents",
    )

    extract_pdfs = commands.add_parser(
        "extract-pdfs",
        help="Extract registered PDFs into hierarchical sections and searchable chunks.",
    )
    extract_pdfs.add_argument("--document-id")
    extract_pdfs.add_argument("--force", action="store_true")
    extract_pdfs.add_argument("--max-chunk-characters", type=int, default=6000)

    discover_topics = commands.add_parser(
        "discover-topics",
        help="Create reviewable topic candidates from extracted document hierarchy.",
    )
    discover_topics.add_argument(
        "--document-type",
        action="append",
        dest="document_types",
        help="Source class to inspect. Repeatable; default: training.",
    )
    discover_topics.add_argument("--document-id")
    discover_topics.add_argument("--force", action="store_true")

    promote_topics = commands.add_parser(
        "promote-topics",
        help="Promote selected topic candidates into canonical topics.",
    )
    promote_topics.add_argument("--candidate-id", action="append", default=[])
    promote_topics.add_argument(
        "--all",
        action="store_true",
        dest="accept_all",
        help="Explicitly promote every eligible candidate.",
    )
    promote_topics.add_argument("--aircraft", default="737 NG")
    promote_topics.add_argument("--minimum-confidence", type=float, default=0.98)

    add_alias = commands.add_parser(
        "add-topic-alias",
        help="Add an exact-search alias to an existing canonical topic.",
    )
    add_alias.add_argument("--topic-id", required=True)
    add_alias.add_argument("--alias", required=True)

    enrich = commands.add_parser(
        "enrich-topics",
        help="Search supporting documents for canonical topic evidence.",
    )
    enrich.add_argument("--topic-id", action="append", default=[])
    enrich.add_argument(
        "--document-type",
        action="append",
        dest="document_types",
        help="Source class. Repeatable; defaults: training, amm.",
    )
    enrich.add_argument("--max-evidence-per-document", type=int, default=3)
    enrich.add_argument("--force", action="store_true")

    triage = commands.add_parser(
        "triage-evidence",
        help="Score and classify retrieved evidence without approving it.",
    )
    triage.add_argument("--topic-id", action="append", default=[])
    triage.add_argument("--force", action="store_true")

    queue = commands.add_parser(
        "evidence-queue",
        help="Show the evidence records awaiting human review.",
    )
    queue.add_argument(
        "--classification",
        choices=(
            "procedure_candidate",
            "supporting_reference",
            "incidental",
            "manual_review",
        ),
    )
    queue.add_argument(
        "--status",
        choices=("needs_review", "approved", "rejected"),
        default="needs_review",
    )
    queue.add_argument("--limit", type=int, default=100)

    decide = commands.add_parser(
        "review-evidence",
        help="Approve or reject one evidence record from the review queue.",
    )
    decide.add_argument("--topic-id", required=True)
    decide.add_argument("--chunk-id", required=True)
    decide.add_argument("--evidence-role", required=True)
    decide.add_argument(
        "--decision",
        required=True,
        choices=("approved", "rejected"),
    )
    decide.add_argument("--reviewer", required=True)

    packet = commands.add_parser(
        "build-research-packet",
        help="Build an approved-evidence-only Markdown research packet.",
    )
    packet.add_argument("--topic-id", required=True)
    packet.add_argument(
        "--output-root",
        default=str(DEFAULT_PACKET_ROOT),
    )
    packet.add_argument(
        "--source-profile",
        choices=tuple(SOURCE_PROFILES),
        default="core",
        help="core includes Training and AMM; all-approved is an explicit override.",
    )

    claim = commands.add_parser(
        "add-content-claim",
        help="Add an evidence-linked claim to the reviewable OKF content form.",
    )
    claim.add_argument("--topic-id", required=True)
    claim.add_argument(
        "--section",
        required=True,
        choices=(
            "overview",
            "system_flow",
            "components",
            "control_logic",
            "maintenance_context",
            "applicability",
        ),
    )
    claim.add_argument("--text", required=True)
    claim.add_argument("--chunk-id", action="append", required=True)
    claim.add_argument("--sort-order", type=int, default=0)
    claim.add_argument("--applicability")

    review_claim = commands.add_parser(
        "review-content-claim",
        help="Approve or reject one structured content claim.",
    )
    review_claim.add_argument("--claim-id", required=True)
    review_claim.add_argument(
        "--decision",
        required=True,
        choices=("approved", "rejected"),
    )
    review_claim.add_argument("--reviewer", required=True)

    preview = commands.add_parser(
        "build-okf-preview",
        help="Build a reviewable Markdown content form before UI implementation.",
    )
    preview.add_argument("--topic-id", required=True)
    preview.add_argument(
        "--output-root",
        default=str(DEFAULT_OKF_PREVIEW_ROOT),
    )

    approve_model = commands.add_parser(
        "review-content-model",
        help="Approve or reject the latest OKF content-model artifact.",
    )
    approve_model.add_argument("--topic-id", required=True)
    approve_model.add_argument(
        "--decision",
        required=True,
        choices=("approved", "rejected"),
    )
    approve_model.add_argument("--reviewer", required=True)

    editorial = commands.add_parser(
        "build-editorial-draft",
        help="Build reader-facing Markdown from an approved content model.",
    )
    editorial.add_argument("--topic-id", required=True)
    editorial.add_argument(
        "--output-root",
        default=str(DEFAULT_EDITORIAL_DRAFT_ROOT),
    )

    register = commands.add_parser(
        "register-document",
        help="Register a local source document and its current version.",
    )
    register.add_argument("path", help="Path to the local source document.")
    register.add_argument("--id", required=True, help="Stable document identifier.")
    register.add_argument("--type", required=True, dest="document_type")
    register.add_argument("--title", required=True)
    register.add_argument("--authority")
    register.add_argument("--revision")
    register.add_argument("--effective-date")
    register.add_argument("--status", choices=("registered", "current", "superseded"), default="registered")
    register.add_argument("--aircraft", action="append", default=[])
    register.add_argument("--operator", default="")

    search = commands.add_parser("search", help="Search indexed source chunks with FTS5.")
    search.add_argument("query")
    search.add_argument("--document-type")
    search.add_argument("--aircraft")
    search.add_argument("--operator")
    search.add_argument("--limit", type=int, default=20)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_path = Path(args.db)

    if args.command != "init-db" and not database_path.exists():
        raise SystemExit(f"Database does not exist: {database_path}. Run init-db first.")

    with KnowledgeStore(database_path) as store:
        store.initialize()
        if args.command == "init-db":
            print(f"Initialized {database_path} with schema version {store.schema_version()}.")
            return 0

        if args.command == "status":
            print(json.dumps(store.status_counts(), indent=2, sort_keys=True))
            return 0

        if args.command == "register-sources":
            registered = register_source_tree(store, args.source_root)
            print(
                json.dumps(
                    {
                        "processed": len(registered),
                        "source_root": str(Path(args.source_root).resolve()),
                        "documents": [
                            {
                                "document_id": source.document_id,
                                "version_id": source.version_id,
                                "document_type": source.document_type,
                                "file_name": source.path.name,
                            }
                            for source in registered
                        ],
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "extract-pdfs":
            results = extract_registered_pdfs(
                store,
                document_id=args.document_id,
                force=args.force,
                max_chunk_characters=args.max_chunk_characters,
                progress=lambda message: print(message, file=sys.stderr, flush=True),
            )
            failed = [result for result in results if result.status == "failed"]
            print(
                json.dumps(
                    {
                        "processed": len(results),
                        "completed": sum(
                            result.status == "completed" for result in results
                        ),
                        "skipped": sum(
                            result.status == "skipped" for result in results
                        ),
                        "failed": len(failed),
                        "results": [
                            {
                                "document_id": result.document_id,
                                "version_id": result.version_id,
                                "status": result.status,
                                "page_count": result.page_count,
                                "section_count": result.section_count,
                                "chunk_count": result.chunk_count,
                                "blank_page_count": result.blank_page_count,
                                "used_outline": result.used_outline,
                                "error": result.error,
                            }
                            for result in results
                        ],
                    },
                    indent=2,
                )
            )
            return 1 if failed else 0

        if args.command == "discover-topics":
            results = discover_topic_candidates(
                store,
                document_types=args.document_types or DEFAULT_TOPIC_SOURCE_TYPES,
                document_id=args.document_id,
                force=args.force,
                progress=lambda message: print(message, file=sys.stderr, flush=True),
            )
            failed = [result for result in results if result.status == "failed"]
            print(
                json.dumps(
                    {
                        "processed": len(results),
                        "completed": sum(
                            result.status == "completed" for result in results
                        ),
                        "skipped": sum(
                            result.status == "skipped" for result in results
                        ),
                        "failed": len(failed),
                        "candidate_count": sum(
                            result.candidate_count for result in results
                        ),
                        "results": [
                            {
                                "document_id": result.document_id,
                                "version_id": result.version_id,
                                "document_type": result.document_type,
                                "status": result.status,
                                "candidate_count": result.candidate_count,
                                "error": result.error,
                            }
                            for result in results
                        ],
                    },
                    indent=2,
                )
            )
            return 1 if failed else 0

        if args.command == "promote-topics":
            try:
                results = promote_topic_candidates(
                    store,
                    aircraft=args.aircraft,
                    candidate_ids=args.candidate_id,
                    accept_all=args.accept_all,
                    minimum_confidence=args.minimum_confidence,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "promoted": len(results),
                        "aircraft": args.aircraft,
                        "topics": [
                            {
                                "candidate_id": result.candidate_id,
                                "topic_id": result.topic_id,
                                "title": result.title,
                                "slug": result.slug,
                                "ata": list(result.ata),
                            }
                            for result in results
                        ],
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "add-topic-alias":
            if not store.canonical_topic_rows(topic_ids=[args.topic_id]):
                raise SystemExit(f"Unknown topic ID: {args.topic_id}")
            store.add_topic_aliases(args.topic_id, [args.alias])
            print(
                json.dumps(
                    {
                        "topic_id": args.topic_id,
                        "aliases": store.aliases_for_topic(args.topic_id),
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "enrich-topics":
            try:
                results = enrich_topics(
                    store,
                    topic_ids=args.topic_id,
                    document_types=args.document_types or DEFAULT_ENRICHMENT_TYPES,
                    max_evidence_per_document=args.max_evidence_per_document,
                    force=args.force,
                    progress=lambda message: print(
                        message,
                        file=sys.stderr,
                        flush=True,
                    ),
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "processed": len(results),
                        "found": sum(result.status == "found" for result in results),
                        "not_found": sum(
                            result.status == "not_found" for result in results
                        ),
                        "skipped": sum(
                            result.status == "skipped" for result in results
                        ),
                        "evidence_count": sum(
                            result.evidence_count for result in results
                        ),
                        "results": [
                            {
                                "topic_id": result.topic_id,
                                "version_id": result.version_id,
                                "document_type": result.document_type,
                                "status": result.status,
                                "evidence_count": result.evidence_count,
                            }
                            for result in results
                        ],
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "triage-evidence":
            results = triage_evidence(
                store,
                topic_ids=args.topic_id,
                force=args.force,
            )
            by_classification: dict[str, int] = {}
            for result in results:
                by_classification[result.classification] = (
                    by_classification.get(result.classification, 0) + 1
                )
            print(
                json.dumps(
                    {
                        "processed": len(results),
                        "stored": sum(result.stored for result in results),
                        "preserved_reviewed": sum(
                            not result.stored for result in results
                        ),
                        "classifications": dict(sorted(by_classification.items())),
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "evidence-queue":
            try:
                rows = store.evidence_review_queue(
                    classification=args.classification,
                    review_status=args.status,
                    limit=args.limit,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(render_review_rows(rows), indent=2))
            return 0

        if args.command == "review-evidence":
            try:
                store.decide_evidence_review(
                    topic_id=args.topic_id,
                    chunk_id=args.chunk_id,
                    evidence_role=args.evidence_role,
                    review_status=args.decision,
                    reviewer=args.reviewer,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "topic_id": args.topic_id,
                        "chunk_id": args.chunk_id,
                        "evidence_role": args.evidence_role,
                        "decision": args.decision,
                        "reviewer": args.reviewer,
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "build-research-packet":
            try:
                result = build_research_packet(
                    store,
                    topic_id=args.topic_id,
                    output_root=args.output_root,
                    source_profile=args.source_profile,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "topic_id": result.topic_id,
                        "title": result.title,
                        "path": result.path,
                        "source_profile": result.source_profile,
                        "approved_evidence_count": result.approved_evidence_count,
                        "excluded_approved_evidence_count": (
                            result.excluded_approved_evidence_count
                        ),
                        "content_hash": result.content_hash,
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "add-content-claim":
            try:
                claim_id = store.upsert_content_claim(
                    topic_id=args.topic_id,
                    section_key=args.section,
                    claim_text=args.text,
                    chunk_ids=args.chunk_id,
                    sort_order=args.sort_order,
                    applicability=args.applicability,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "claim_id": claim_id,
                        "topic_id": args.topic_id,
                        "section": args.section,
                        "review_status": "needs_review",
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "review-content-claim":
            try:
                store.decide_content_claim(
                    claim_id=args.claim_id,
                    review_status=args.decision,
                    reviewer=args.reviewer,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "claim_id": args.claim_id,
                        "decision": args.decision,
                        "reviewer": args.reviewer,
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "build-okf-preview":
            try:
                result = build_okf_preview(
                    store,
                    topic_id=args.topic_id,
                    output_root=args.output_root,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "topic_id": result.topic_id,
                        "title": result.title,
                        "path": result.path,
                        "claim_count": result.claim_count,
                        "approved_claim_count": result.approved_claim_count,
                        "content_hash": result.content_hash,
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "review-content-model":
            try:
                artifact = store.decide_latest_artifact(
                    topic_id=args.topic_id,
                    artifact_type="okf_preview",
                    review_status=args.decision,
                    reviewer=args.reviewer,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "topic_id": args.topic_id,
                        "artifact_id": artifact["id"],
                        "content_hash": artifact["content_hash"],
                        "decision": artifact["review_status"],
                        "reviewer": artifact["reviewer"],
                        "reviewed_at": artifact["reviewed_at"],
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "build-editorial-draft":
            try:
                result = build_editorial_draft(
                    store,
                    topic_id=args.topic_id,
                    output_root=args.output_root,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(
                json.dumps(
                    {
                        "topic_id": result.topic_id,
                        "title": result.title,
                        "path": result.path,
                        "content_model_hash": result.content_model_hash,
                        "claim_count": result.claim_count,
                        "source_count": result.source_count,
                        "content_hash": result.content_hash,
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "register-document":
            path = Path(args.path).resolve()
            if not path.is_file():
                raise SystemExit(f"Source document does not exist: {path}")
            store.register_document(
                document_id=args.id,
                file_name=path.name,
                title=args.title,
                document_type=args.document_type,
                source_authority=args.authority,
            )
            version_id = store.register_document_version(
                document_id=args.id,
                checksum=sha256_file(path),
                revision=args.revision,
                effective_date=args.effective_date,
                local_path=str(path),
                status=args.status,
                aircraft=args.aircraft,
                operator=args.operator,
            )
            print(f"Registered {args.id} as version {version_id}.")
            return 0

        if args.command == "search":
            results = store.search_chunks(
                args.query,
                document_type=args.document_type,
                aircraft=args.aircraft,
                operator=args.operator,
                limit=args.limit,
            )
            print(
                json.dumps(
                    [
                        {
                            "chunk_id": result.chunk_id,
                            "document_id": result.document_id,
                            "document_version_id": result.document_version_id,
                            "document_type": result.document_type,
                            "heading": result.heading,
                            "hierarchy_path": result.hierarchy_path,
                            "pdf_pages": result.pdf_pages,
                            "printed_pages": result.printed_pages,
                            "rank": result.rank,
                            "excerpt": result.content[:300],
                        }
                        for result in results
                    ],
                    indent=2,
                )
            )
            return 0

    return 1
