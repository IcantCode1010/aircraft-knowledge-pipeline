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
        if args.command == "init-db":
            store.initialize()
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
