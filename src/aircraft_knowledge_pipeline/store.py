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
        version_id: str | None = None,
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
        if version_id:
            clauses.append("v.id = ?")
            parameters.append(version_id)
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

    def candidate_rows(
        self,
        *,
        candidate_ids: Sequence[str] = (),
        status: str = "candidate",
        minimum_confidence: float = 0.0,
    ) -> list[sqlite3.Row]:
        clauses = ["tc.status = ?", "COALESCE(tc.confidence, 0) >= ?"]
        parameters: list[object] = [status, minimum_confidence]
        if candidate_ids:
            placeholders = ", ".join("?" for _ in candidate_ids)
            clauses.append(f"tc.id IN ({placeholders})")
            parameters.extend(candidate_ids)
        return self.connection.execute(
            f"""
            SELECT
                tc.id,
                tc.version_id,
                tc.section_id,
                tc.title,
                tc.hierarchy_path,
                tc.confidence,
                d.document_type
            FROM topic_candidates tc
            JOIN document_versions dv ON dv.id = tc.version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY tc.hierarchy_path, tc.title
            """,
            parameters,
        ).fetchall()

    def accept_topic_candidate(self, candidate_id: str, topic_id: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE topic_candidates
                SET status = 'accepted', proposed_topic_id = ?
                WHERE id = ? AND status IN ('candidate', 'accepted')
                """,
                (topic_id, candidate_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"Candidate is not available for acceptance: {candidate_id}")

    def canonical_topic_rows(
        self,
        *,
        topic_ids: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[object] = []
        if topic_ids:
            placeholders = ", ".join("?" for _ in topic_ids)
            clauses.append(f"t.id IN ({placeholders})")
            parameters.extend(topic_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.connection.execute(
            f"""
            SELECT
                t.id,
                t.slug,
                t.title,
                t.aircraft,
                t.ata_json,
                t.review_status
            FROM topics t
            {where}
            ORDER BY t.title
            """,
            parameters,
        ).fetchall()

    def aliases_for_topic(self, topic_id: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT alias
            FROM topic_aliases
            WHERE topic_id = ?
            ORDER BY CASE WHEN normalized_alias = (
                SELECT normalized_alias
                FROM topic_aliases
                WHERE topic_id = ?
                ORDER BY created_at, normalized_alias
                LIMIT 1
            ) THEN 0 ELSE 1 END, normalized_alias
            """,
            (topic_id, topic_id),
        ).fetchall()
        return [str(row["alias"]) for row in rows]

    def enrichment_source_versions(
        self,
        *,
        document_types: Sequence[str],
    ) -> list[sqlite3.Row]:
        if not document_types:
            raise ValueError("At least one enrichment document type is required.")
        placeholders = ", ".join("?" for _ in document_types)
        return self.connection.execute(
            f"""
            SELECT
                d.id AS document_id,
                d.document_type,
                dv.id AS version_id,
                dv.checksum
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE d.document_type IN ({placeholders})
              AND EXISTS (
                  SELECT 1 FROM source_chunks c WHERE c.version_id = dv.id
              )
            ORDER BY d.document_type, d.id
            """,
            [value.lower() for value in document_types],
        ).fetchall()

    def topic_search_id(
        self,
        *,
        topic_id: str,
        version_id: str,
        search_type: str,
        query_fingerprint: str,
        processor_version: str,
    ) -> str | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM topic_searches
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
        return str(row["id"]) if row else None

    def add_topic_evidence(
        self,
        *,
        topic_id: str,
        chunk_id: str,
        search_id: str,
        evidence_role: str,
        confidence: float | None = None,
        review_status: str = "needs_review",
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO topic_evidence (
                    topic_id, chunk_id, search_id, evidence_role,
                    confidence, review_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id, chunk_id, evidence_role) DO UPDATE SET
                    search_id = excluded.search_id,
                    confidence = excluded.confidence,
                    review_status = CASE
                        WHEN topic_evidence.review_status IN ('approved', 'rejected')
                        THEN topic_evidence.review_status
                        ELSE excluded.review_status
                    END
                """,
                (
                    topic_id,
                    chunk_id,
                    search_id,
                    evidence_role,
                    confidence,
                    review_status,
                    utc_now(),
                ),
            )

    def clear_topic_evidence_for_version(
        self,
        *,
        topic_id: str,
        version_id: str,
        evidence_role: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM topic_evidence
                WHERE topic_id = ? AND evidence_role = ?
                  AND review_status = 'needs_review'
                  AND chunk_id IN (
                      SELECT id FROM source_chunks WHERE version_id = ?
                  )
                """,
                (topic_id, evidence_role, version_id),
            )

    def evidence_rows_for_review(
        self,
        *,
        topic_ids: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[object] = []
        if topic_ids:
            placeholders = ", ".join("?" for _ in topic_ids)
            clauses.append(f"te.topic_id IN ({placeholders})")
            parameters.extend(topic_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.connection.execute(
            f"""
            SELECT
                te.topic_id,
                te.chunk_id,
                te.evidence_role,
                t.title AS topic_title,
                d.document_type,
                c.heading,
                c.hierarchy_path,
                c.content,
                c.pdf_pages_json,
                c.printed_pages_json
            FROM topic_evidence te
            JOIN topics t ON t.id = te.topic_id
            JOIN source_chunks c ON c.id = te.chunk_id
            JOIN document_versions dv ON dv.id = c.version_id
            JOIN documents d ON d.id = dv.document_id
            {where}
            ORDER BY t.title, d.document_type, c.ordinal
            """,
            parameters,
        ).fetchall()

    def upsert_evidence_review(
        self,
        *,
        topic_id: str,
        chunk_id: str,
        evidence_role: str,
        classification: str,
        score: float,
        reasons: Sequence[str],
        processor_version: str,
        force: bool = False,
    ) -> bool:
        existing = self.connection.execute(
            """
            SELECT review_status, processor_version
            FROM evidence_reviews
            WHERE topic_id = ? AND chunk_id = ? AND evidence_role = ?
            """,
            (topic_id, chunk_id, evidence_role),
        ).fetchone()
        if (
            existing
            and str(existing["review_status"]) != "needs_review"
            and not force
        ):
            return False
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO evidence_reviews (
                    topic_id, chunk_id, evidence_role, classification,
                    score, reasons_json, processor_version, review_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'needs_review', ?, ?)
                ON CONFLICT(topic_id, chunk_id, evidence_role) DO UPDATE SET
                    classification = excluded.classification,
                    score = excluded.score,
                    reasons_json = excluded.reasons_json,
                    processor_version = excluded.processor_version,
                    review_status = CASE
                        WHEN evidence_reviews.review_status = 'needs_review'
                          OR ? THEN 'needs_review'
                        ELSE evidence_reviews.review_status
                    END,
                    reviewer = CASE WHEN ? THEN NULL ELSE evidence_reviews.reviewer END,
                    reviewed_at = CASE WHEN ? THEN NULL ELSE evidence_reviews.reviewed_at END,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    chunk_id,
                    evidence_role,
                    classification,
                    score,
                    json.dumps(list(reasons)),
                    processor_version,
                    now,
                    now,
                    int(force),
                    int(force),
                    int(force),
                ),
            )
        return True

    def evidence_review_queue(
        self,
        *,
        classification: str | None = None,
        review_status: str = "needs_review",
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        if limit < 1 or limit > 1000:
            raise ValueError("Review queue limit must be between 1 and 1000.")
        clauses = ["er.review_status = ?"]
        parameters: list[object] = [review_status]
        if classification:
            clauses.append("er.classification = ?")
            parameters.append(classification)
        parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT
                er.topic_id,
                t.title AS topic_title,
                er.chunk_id,
                er.evidence_role,
                d.document_type,
                er.classification,
                er.score,
                er.reasons_json,
                er.review_status,
                c.heading,
                c.hierarchy_path,
                c.pdf_pages_json,
                c.printed_pages_json,
                substr(c.content, 1, 500) AS excerpt
            FROM evidence_reviews er
            JOIN topics t ON t.id = er.topic_id
            JOIN source_chunks c ON c.id = er.chunk_id
            JOIN document_versions dv ON dv.id = c.version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY
                CASE er.classification
                    WHEN 'procedure_candidate' THEN 0
                    WHEN 'supporting_reference' THEN 1
                    WHEN 'manual_review' THEN 2
                    ELSE 3
                END,
                er.score DESC,
                t.title,
                c.ordinal
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    def decide_evidence_review(
        self,
        *,
        topic_id: str,
        chunk_id: str,
        evidence_role: str,
        review_status: str,
        reviewer: str,
    ) -> None:
        if review_status not in {"approved", "rejected"}:
            raise ValueError("Evidence decision must be approved or rejected.")
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE evidence_reviews
                SET review_status = ?, reviewer = ?, reviewed_at = ?, updated_at = ?
                WHERE topic_id = ? AND chunk_id = ? AND evidence_role = ?
                """,
                (
                    review_status,
                    reviewer,
                    now,
                    now,
                    topic_id,
                    chunk_id,
                    evidence_role,
                ),
            )
            if cursor.rowcount == 1:
                self.connection.execute(
                    """
                    UPDATE topic_evidence
                    SET review_status = ?
                    WHERE topic_id = ? AND chunk_id = ? AND evidence_role = ?
                    """,
                    (review_status, topic_id, chunk_id, evidence_role),
                )
        if cursor.rowcount != 1:
            raise ValueError("Evidence review record was not found.")

    def approved_evidence_for_topic(
        self,
        topic_id: str,
        *,
        document_types: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses = ["er.topic_id = ?", "er.review_status = 'approved'"]
        parameters: list[object] = [topic_id]
        if document_types:
            placeholders = ", ".join("?" for _ in document_types)
            clauses.append(f"d.document_type IN ({placeholders})")
            parameters.extend(value.lower() for value in document_types)
        return self.connection.execute(
            f"""
            SELECT
                er.topic_id,
                t.title AS topic_title,
                t.aircraft,
                t.ata_json,
                er.chunk_id,
                er.evidence_role,
                er.classification,
                er.score,
                er.reviewer,
                er.reviewed_at,
                d.id AS document_id,
                d.title AS document_title,
                d.file_name,
                d.document_type,
                dv.id AS version_id,
                c.heading,
                c.hierarchy_path,
                c.content,
                c.pdf_pages_json,
                c.printed_pages_json
            FROM evidence_reviews er
            JOIN topics t ON t.id = er.topic_id
            JOIN source_chunks c ON c.id = er.chunk_id
            JOIN document_versions dv ON dv.id = c.version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY
                CASE d.document_type
                    WHEN 'training' THEN 0
                    WHEN 'fcom' THEN 1
                    WHEN 'amm' THEN 2
                    WHEN 'mel' THEN 3
                    WHEN 'qrh' THEN 4
                    ELSE 5
                END,
                c.ordinal,
                c.id
            """,
            parameters,
        ).fetchall()

    def upsert_content_claim(
        self,
        *,
        topic_id: str,
        section_key: str,
        claim_text: str,
        chunk_ids: Sequence[str],
        sort_order: int = 0,
        applicability: str | None = None,
        claim_id: str | None = None,
    ) -> str:
        allowed_sections = {
            "overview",
            "system_flow",
            "components",
            "control_logic",
            "maintenance_context",
            "applicability",
        }
        if section_key not in allowed_sections:
            raise ValueError(f"Unknown content claim section: {section_key}")
        claim_text = " ".join(claim_text.split())
        if not claim_text:
            raise ValueError("Claim text cannot be blank.")
        if not chunk_ids:
            raise ValueError("A content claim requires at least one evidence chunk.")
        unique_chunk_ids = tuple(dict.fromkeys(chunk_ids))
        placeholders = ", ".join("?" for _ in unique_chunk_ids)
        approved_rows = self.connection.execute(
            f"""
            SELECT DISTINCT er.chunk_id
            FROM evidence_reviews er
            JOIN source_chunks c ON c.id = er.chunk_id
            JOIN document_versions dv ON dv.id = c.version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE er.topic_id = ?
              AND er.review_status = 'approved'
              AND d.document_type IN ('training', 'amm')
              AND er.chunk_id IN ({placeholders})
            """,
            (topic_id, *unique_chunk_ids),
        ).fetchall()
        approved_chunk_ids = {str(row["chunk_id"]) for row in approved_rows}
        missing = sorted(set(unique_chunk_ids) - approved_chunk_ids)
        if missing:
            raise ValueError(
                "Claims may use only approved Training/AMM evidence for the topic. "
                "Unapproved chunk IDs: "
                + ", ".join(missing)
            )
        claim_id = claim_id or str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"akp:claim:{topic_id}:{section_key}:{claim_text}",
            )
        )
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO claims (
                    id, topic_id, claim_text, review_status, created_at, updated_at
                ) VALUES (?, ?, ?, 'needs_review', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    topic_id = excluded.topic_id,
                    claim_text = excluded.claim_text,
                    updated_at = excluded.updated_at
                """,
                (claim_id, topic_id, claim_text, now, now),
            )
            self.connection.execute(
                """
                INSERT INTO claim_metadata (
                    claim_id, section_key, sort_order, applicability
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    section_key = excluded.section_key,
                    sort_order = excluded.sort_order,
                    applicability = excluded.applicability
                """,
                (claim_id, section_key, sort_order, applicability),
            )
            self.connection.execute(
                "DELETE FROM claim_evidence WHERE claim_id = ?",
                (claim_id,),
            )
            self.connection.executemany(
                """
                INSERT INTO claim_evidence (
                    claim_id, chunk_id, created_at
                ) VALUES (?, ?, ?)
                """,
                [(claim_id, chunk_id, now) for chunk_id in unique_chunk_ids],
            )
        return claim_id

    def decide_content_claim(
        self,
        *,
        claim_id: str,
        review_status: str,
        reviewer: str,
    ) -> None:
        if review_status not in {"approved", "rejected"}:
            raise ValueError("Claim decision must be approved or rejected.")
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE claims
                SET review_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (review_status, now, claim_id),
            )
            if cursor.rowcount == 1:
                self.connection.execute(
                    """
                    UPDATE claim_metadata
                    SET reviewer = ?, reviewed_at = ?
                    WHERE claim_id = ?
                    """,
                    (reviewer, now, claim_id),
                )
        if cursor.rowcount != 1:
            raise ValueError(f"Content claim was not found: {claim_id}")

    def content_claim_rows(self, topic_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                c.id AS claim_id,
                c.claim_text,
                c.review_status,
                cm.section_key,
                cm.sort_order,
                cm.applicability,
                cm.reviewer,
                cm.reviewed_at,
                ce.chunk_id,
                d.document_type,
                d.title AS document_title,
                d.file_name,
                sc.heading,
                sc.pdf_pages_json,
                sc.printed_pages_json
            FROM claims c
            JOIN claim_metadata cm ON cm.claim_id = c.id
            JOIN claim_evidence ce ON ce.claim_id = c.id
            JOIN source_chunks sc ON sc.id = ce.chunk_id
            JOIN document_versions dv ON dv.id = sc.version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE c.topic_id = ?
            ORDER BY
                CASE cm.section_key
                    WHEN 'overview' THEN 0
                    WHEN 'system_flow' THEN 1
                    WHEN 'components' THEN 2
                    WHEN 'control_logic' THEN 3
                    WHEN 'maintenance_context' THEN 4
                    WHEN 'applicability' THEN 5
                    ELSE 6
                END,
                cm.sort_order,
                c.id,
                d.document_type,
                sc.ordinal
            """,
            (topic_id,),
        ).fetchall()

    def evidence_review_summary(self, topic_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT review_status, COUNT(*) AS count
            FROM evidence_reviews
            WHERE topic_id = ?
            GROUP BY review_status
            """,
            (topic_id,),
        ).fetchall()
        return {str(row["review_status"]): int(row["count"]) for row in rows}

    def record_artifact(
        self,
        *,
        artifact_id: str,
        topic_id: str,
        artifact_type: str,
        artifact_path: str,
        content_hash: str,
        schema_version: str,
        review_status: str = "needs_review",
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM artifacts
                WHERE topic_id = ? AND artifact_type = ? AND artifact_path = ?
                  AND content_hash <> ?
                """,
                (topic_id, artifact_type, artifact_path, content_hash),
            )
            self.connection.execute(
                """
                INSERT INTO artifacts (
                    id, topic_id, artifact_type, artifact_path, content_hash,
                    schema_version, review_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_type, artifact_path, content_hash) DO UPDATE SET
                    topic_id = excluded.topic_id,
                    schema_version = excluded.schema_version,
                    review_status = CASE
                        WHEN artifacts.review_status IN ('approved', 'rejected')
                        THEN artifacts.review_status
                        ELSE excluded.review_status
                    END
                """,
                (
                    artifact_id,
                    topic_id,
                    artifact_type,
                    artifact_path,
                    content_hash,
                    schema_version,
                    review_status,
                    utc_now(),
                ),
            )

    def decide_latest_artifact(
        self,
        *,
        topic_id: str,
        artifact_type: str,
        review_status: str,
        reviewer: str,
    ) -> sqlite3.Row:
        if review_status not in {"approved", "rejected"}:
            raise ValueError("Artifact decision must be approved or rejected.")
        artifact = self.connection.execute(
            """
            SELECT *
            FROM artifacts
            WHERE topic_id = ? AND artifact_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (topic_id, artifact_type),
        ).fetchone()
        if not artifact:
            raise ValueError(
                f"No {artifact_type} artifact exists for topic: {topic_id}"
            )
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE artifacts SET review_status = ? WHERE id = ?",
                (review_status, artifact["id"]),
            )
            self.connection.execute(
                """
                INSERT INTO artifact_reviews (
                    artifact_id, review_status, reviewer, reviewed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    review_status = excluded.review_status,
                    reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at
                """,
                (artifact["id"], review_status, reviewer, now),
            )
        return self.connection.execute(
            """
            SELECT a.*, ar.reviewer, ar.reviewed_at
            FROM artifacts a
            JOIN artifact_reviews ar ON ar.artifact_id = a.id
            WHERE a.id = ?
            """,
            (artifact["id"],),
        ).fetchone()

    def approved_artifact(
        self,
        *,
        topic_id: str,
        artifact_type: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT a.*, ar.reviewer, ar.reviewed_at
            FROM artifacts a
            JOIN artifact_reviews ar ON ar.artifact_id = a.id
            WHERE a.topic_id = ?
              AND a.artifact_type = ?
              AND a.review_status = 'approved'
              AND ar.review_status = 'approved'
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT 1
            """,
            (topic_id, artifact_type),
        ).fetchone()

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
            ("evidence", "topic_evidence"),
            ("evidence_reviews", "evidence_reviews"),
            ("claims", "claims"),
            ("jobs", "processing_jobs"),
            ("artifacts", "artifacts"),
        ):
            row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[label] = int(row["count"])
        return counts
