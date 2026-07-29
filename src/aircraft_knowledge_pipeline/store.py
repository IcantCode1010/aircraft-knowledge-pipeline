from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Iterable, Sequence


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_alias(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def safe_fts_query(value: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    if not tokens:
        raise ValueError("Search query must contain at least one letter or number.")
    return " AND ".join(f'"{token}"' for token in tokens)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    document_id: str
    document_version_id: str
    document_type: str
    heading: str
    hierarchy_path: str
    content: str
    pdf_pages: list[int]
    printed_pages: list[str]
    rank: float


@dataclass(frozen=True)
class ExtractionSection:
    id: str
    parent_id: str | None
    ordinal: int
    title: str
    hierarchy_path: str
    pdf_page_start: int | None
    pdf_page_end: int | None
    printed_page_start: str | None = None
    printed_page_end: str | None = None


@dataclass(frozen=True)
class ExtractionChunk:
    id: str
    section_id: str | None
    ordinal: int
    heading: str
    hierarchy_path: str
    content: str
    pdf_pages: Sequence[int]
    printed_pages: Sequence[str]
    content_hash: str
    token_count: int | None = None


@dataclass(frozen=True)
class TopicCandidate:
    id: str
    section_id: str | None
    title: str
    hierarchy_path: str
    confidence: float | None = None


class KnowledgeStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> KnowledgeStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize(self) -> None:
        schema = files("aircraft_knowledge_pipeline").joinpath("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.commit()

    def schema_version(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        return str(row["value"]) if row else None

    def register_document(
        self,
        *,
        document_id: str,
        file_name: str,
        title: str,
        document_type: str,
        source_authority: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO documents (
                    id, file_name, title, document_type, source_authority, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    file_name = excluded.file_name,
                    title = excluded.title,
                    document_type = excluded.document_type,
                    source_authority = excluded.source_authority
                """,
                (
                    document_id,
                    file_name,
                    title,
                    document_type.lower(),
                    source_authority,
                    utc_now(),
                ),
            )

    def register_document_version(
        self,
        *,
        document_id: str,
        checksum: str,
        revision: str | None = None,
        effective_date: str | None = None,
        local_path: str | None = None,
        status: str = "registered",
        version_id: str | None = None,
        aircraft: Sequence[str] = (),
        operator: str = "",
    ) -> str:
        version_id = version_id or f"{document_id}-{checksum[:12]}"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO document_versions (
                    id, document_id, checksum, revision, effective_date,
                    local_path, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, checksum) DO UPDATE SET
                    revision = excluded.revision,
                    effective_date = excluded.effective_date,
                    local_path = excluded.local_path,
                    status = excluded.status
                """,
                (
                    version_id,
                    document_id,
                    checksum,
                    revision,
                    effective_date,
                    local_path,
                    status,
                    utc_now(),
                ),
            )
            stored = self.connection.execute(
                """
                SELECT id FROM document_versions
                WHERE document_id = ? AND checksum = ?
                """,
                (document_id, checksum),
            ).fetchone()
            if stored is None:
                raise RuntimeError("Document version was not stored.")
            stored_version_id = str(stored["id"])
            for aircraft_name in aircraft:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO document_scopes(version_id, aircraft, operator)
                    VALUES (?, ?, ?)
                    """,
                    (stored_version_id, aircraft_name, operator),
                )
        return stored_version_id

    def add_section(
        self,
        *,
        version_id: str,
        title: str,
        hierarchy_path: str,
        ordinal: int,
        parent_id: str | None = None,
        pdf_page_start: int | None = None,
        pdf_page_end: int | None = None,
        printed_page_start: str | None = None,
        printed_page_end: str | None = None,
        section_id: str | None = None,
    ) -> str:
        section_id = section_id or str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO document_sections (
                    id, version_id, parent_id, ordinal, title, hierarchy_path,
                    pdf_page_start, pdf_page_end, printed_page_start,
                    printed_page_end, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id,
                    version_id,
                    parent_id,
                    ordinal,
                    title,
                    hierarchy_path,
                    pdf_page_start,
                    pdf_page_end,
                    printed_page_start,
                    printed_page_end,
                    utc_now(),
                ),
            )
        return section_id

    def add_chunk(
        self,
        *,
        version_id: str,
        heading: str,
        hierarchy_path: str,
        content: str,
        ordinal: int,
        section_id: str | None = None,
        pdf_pages: Sequence[int] = (),
        printed_pages: Sequence[str] = (),
        token_count: int | None = None,
        chunk_id: str | None = None,
    ) -> str:
        content_hash = sha256_text(
            json.dumps(
                {
                    "heading": heading,
                    "hierarchy_path": hierarchy_path,
                    "content": content,
                    "pdf_pages": list(pdf_pages),
                    "printed_pages": list(printed_pages),
                },
                sort_keys=True,
            )
        )
        chunk_id = chunk_id or str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO source_chunks (
                    id, version_id, section_id, ordinal, heading, hierarchy_path,
                    content, pdf_pages_json, printed_pages_json, content_hash,
                    token_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id, content_hash) DO NOTHING
                """,
                (
                    chunk_id,
                    version_id,
                    section_id,
                    ordinal,
                    heading,
                    hierarchy_path,
                    content,
                    json.dumps(list(pdf_pages)),
                    json.dumps(list(printed_pages)),
                    content_hash,
                    token_count,
                    utc_now(),
                ),
            )
            stored = self.connection.execute(
                """
                SELECT id FROM source_chunks
                WHERE version_id = ? AND content_hash = ?
                """,
                (version_id, content_hash),
            ).fetchone()
        if stored is None:
            raise RuntimeError("Source chunk was not stored.")
        return str(stored["id"])

    def create_topic(
        self,
        *,
        topic_id: str,
        slug: str,
        title: str,
        aircraft: str,
        ata: Sequence[str] = (),
        review_status: str = "needs_review",
    ) -> None:
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO topics (
                    id, slug, title, aircraft, ata_json, review_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    title = excluded.title,
                    aircraft = excluded.aircraft,
                    ata_json = excluded.ata_json,
                    review_status = excluded.review_status,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    slug,
                    title,
                    aircraft,
                    json.dumps(list(ata)),
                    review_status,
                    now,
                    now,
                ),
            )

    def add_topic_aliases(self, topic_id: str, aliases: Iterable[str]) -> None:
        with self.connection:
            for alias in aliases:
                normalized = normalize_alias(alias)
                if not normalized:
                    continue
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO topic_aliases (
                        topic_id, alias, normalized_alias, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (topic_id, alias, normalized, utc_now()),
                )

    def replace_topic_candidates(
        self,
        *,
        version_id: str,
        candidates: Sequence[TopicCandidate],
    ) -> None:
        """Replace only unreviewed candidates; preserve reviewed decisions."""
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM topic_candidates
                WHERE version_id = ? AND status = 'candidate'
                """,
                (version_id,),
            )
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO topic_candidates (
                    id, version_id, section_id, title, hierarchy_path,
                    confidence, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?)
                """,
                [
                    (
                        candidate.id,
                        version_id,
                        candidate.section_id,
                        candidate.title,
                        candidate.hierarchy_path,
                        candidate.confidence,
                        utc_now(),
                    )
                    for candidate in candidates
                ],
            )

    def topic_source_versions(
        self,
        *,
        document_types: Sequence[str],
        document_id: str | None = None,
    ) -> list[sqlite3.Row]:
        if not document_types:
            raise ValueError("At least one topic source document type is required.")
        placeholders = ", ".join("?" for _ in document_types)
        clauses = [f"d.document_type IN ({placeholders})"]
        parameters: list[object] = [value.lower() for value in document_types]
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM document_sections s WHERE s.version_id = dv.id
            )
            """
        )
        if document_id:
            clauses.append("d.id = ?")
            parameters.append(document_id)
        return self.connection.execute(
            f"""
            SELECT
                d.id AS document_id,
                d.title,
                d.document_type,
                dv.id AS version_id,
                dv.checksum
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY d.document_type, d.id
            """,
            parameters,
        ).fetchall()

    def sections_for_version(self, version_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT id, title, hierarchy_path, pdf_page_start, pdf_page_end
            FROM document_sections
            WHERE version_id = ?
            ORDER BY ordinal, id
            """,
            (version_id,),
        ).fetchall()

    def search_chunks(
        self,
        query: str,
        *,
        document_type: str | None = None,
        aircraft: str | None = None,
        operator: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        if limit < 1 or limit > 200:
            raise ValueError("Search limit must be between 1 and 200.")

        clauses = ["source_chunks_fts MATCH ?"]
        parameters: list[object] = [safe_fts_query(query)]

        if document_type:
            clauses.append("d.document_type = ?")
            parameters.append(document_type.lower())
        if aircraft:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM document_scopes ds
                    WHERE ds.version_id = v.id AND ds.aircraft = ?
                )
                """
            )
            parameters.append(aircraft)
        if operator is not None:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM document_scopes ds
                    WHERE ds.version_id = v.id AND ds.operator = ?
                )
                """
            )
            parameters.append(operator)

        parameters.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT
                c.id AS chunk_id,
                d.id AS document_id,
                v.id AS document_version_id,
                d.document_type,
                c.heading,
                c.hierarchy_path,
                c.content,
                c.pdf_pages_json,
                c.printed_pages_json,
                bm25(source_chunks_fts, 5.0, 2.0, 1.0) AS rank
            FROM source_chunks_fts
            JOIN source_chunks c ON c.rowid = source_chunks_fts.rowid
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY rank
            LIMIT ?
            """,
            parameters,
        ).fetchall()

        return [
            SearchResult(
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                document_version_id=str(row["document_version_id"]),
                document_type=str(row["document_type"]),
                heading=str(row["heading"]),
                hierarchy_path=str(row["hierarchy_path"]),
                content=str(row["content"]),
                pdf_pages=json.loads(row["pdf_pages_json"]),
                printed_pages=json.loads(row["printed_pages_json"]),
                rank=float(row["rank"]),
            )
            for row in rows
        ]

    def record_topic_search(
        self,
        *,
        topic_id: str,
        version_id: str,
        search_type: str,
        query_fingerprint: str,
        status: str,
        processor_version: str,
        searched_aliases: Sequence[str] = (),
        highest_score: float | None = None,
        search_id: str | None = None,
    ) -> str:
        search_id = search_id or str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO topic_searches (
                    id, topic_id, version_id, search_type, query_fingerprint,
                    searched_aliases_json, status, highest_score,
                    processor_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    topic_id, version_id, search_type,
                    query_fingerprint, processor_version
                ) DO UPDATE SET
                    searched_aliases_json = excluded.searched_aliases_json,
                    status = excluded.status,
                    highest_score = excluded.highest_score
                """,
                (
                    search_id,
                    topic_id,
                    version_id,
                    search_type,
                    query_fingerprint,
                    json.dumps(list(searched_aliases)),
                    status,
                    highest_score,
                    processor_version,
                    utc_now(),
                ),
            )
            stored = self.connection.execute(
                """
                SELECT id FROM topic_searches
                WHERE topic_id = ? AND version_id = ? AND search_type = ?
                  AND query_fingerprint = ? AND processor_version = ?
                """,
                (
                    topic_id,
                    version_id,
                    search_type,
                    query_fingerprint,
                    processor_version,
                ),
            ).fetchone()
        if stored is None:
            raise RuntimeError("Topic search result was not stored.")
        return str(stored["id"])

    def create_processing_job(
        self,
        *,
        job_type: str,
        input_type: str,
        input_id: str,
        input_hash: str,
        processor_version: str,
        job_id: str | None = None,
    ) -> tuple[str, bool]:
        job_id = job_id or str(uuid.uuid4())
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO processing_jobs (
                    id, job_type, input_type, input_id, input_hash,
                    processor_version, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(
                    job_type, input_type, input_id, input_hash, processor_version
                ) DO NOTHING
                """,
                (
                    job_id,
                    job_type,
                    input_type,
                    input_id,
                    input_hash,
                    processor_version,
                    utc_now(),
                ),
            )
            stored = self.connection.execute(
                """
                SELECT id FROM processing_jobs
                WHERE job_type = ? AND input_type = ? AND input_id = ?
                  AND input_hash = ? AND processor_version = ?
                """,
                (job_type, input_type, input_id, input_hash, processor_version),
            ).fetchone()
        if stored is None:
            raise RuntimeError("Processing job was not stored.")
        return str(stored["id"]), cursor.rowcount == 1

    def processing_job_status(self, job_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM processing_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return str(row["status"]) if row else None

    def start_processing_job(self, job_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    error_message = NULL,
                    started_at = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (utc_now(), job_id),
            )

    def complete_processing_job(self, job_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'completed', completed_at = ?
                WHERE id = ?
                """,
                (utc_now(), job_id),
            )

    def fail_processing_job(self, job_id: str, error_message: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (error_message[:4000], utc_now(), job_id),
            )

    def reset_processing_job(self, job_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'pending',
                    error_message = NULL,
                    started_at = NULL,
                    completed_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )

    def replace_document_extraction(
        self,
        *,
        version_id: str,
        sections: Sequence[ExtractionSection],
        chunks: Sequence[ExtractionChunk],
    ) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM source_chunks WHERE version_id = ?",
                (version_id,),
            )
            self.connection.execute(
                "DELETE FROM document_sections WHERE version_id = ?",
                (version_id,),
            )

            self.connection.executemany(
                """
                INSERT INTO document_sections (
                    id, version_id, parent_id, ordinal, title, hierarchy_path,
                    pdf_page_start, pdf_page_end, printed_page_start,
                    printed_page_end, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        section.id,
                        version_id,
                        section.parent_id,
                        section.ordinal,
                        section.title,
                        section.hierarchy_path,
                        section.pdf_page_start,
                        section.pdf_page_end,
                        section.printed_page_start,
                        section.printed_page_end,
                        utc_now(),
                    )
                    for section in sections
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO source_chunks (
                    id, version_id, section_id, ordinal, heading, hierarchy_path,
                    content, pdf_pages_json, printed_pages_json, content_hash,
                    token_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.id,
                        version_id,
                        chunk.section_id,
                        chunk.ordinal,
                        chunk.heading,
                        chunk.hierarchy_path,
                        chunk.content,
                        json.dumps(list(chunk.pdf_pages)),
                        json.dumps(list(chunk.printed_pages)),
                        chunk.content_hash,
                        chunk.token_count,
                        utc_now(),
                    )
                    for chunk in chunks
                ],
            )

    def registered_pdf_versions(
        self,
        document_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses = [
            "LOWER(dv.local_path) LIKE '%.pdf'",
            "dv.local_path IS NOT NULL",
        ]
        parameters: list[object] = []
        if document_id:
            clauses.append("d.id = ?")
            parameters.append(document_id)
        return self.connection.execute(
            f"""
            SELECT
                d.id AS document_id,
                d.title,
                d.document_type,
                dv.id AS version_id,
                dv.checksum,
                dv.local_path
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY d.document_type, d.id
            """,
            parameters,
        ).fetchall()

    def status_counts(self) -> dict[str, int | str | None]:
        counts: dict[str, int | str | None] = {"schema_version": self.schema_version()}
        for label, table in (
            ("documents", "documents"),
            ("document_versions", "document_versions"),
            ("sections", "document_sections"),
            ("chunks", "source_chunks"),
            ("topic_candidates", "topic_candidates"),
            ("topics", "topics"),
            ("searches", "topic_searches"),
            ("claims", "claims"),
            ("jobs", "processing_jobs"),
            ("artifacts", "artifacts"),
        ):
            row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[label] = int(row["count"])
        return counts
