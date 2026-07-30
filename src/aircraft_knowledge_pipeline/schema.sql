PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO schema_metadata (key, value)
VALUES ('schema_version', '4')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_authority TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_type
ON documents(document_type);

CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    checksum TEXT NOT NULL,
    revision TEXT,
    effective_date TEXT,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK(status IN ('registered', 'current', 'superseded')),
    created_at TEXT NOT NULL,
    UNIQUE(document_id, checksum)
);

CREATE INDEX IF NOT EXISTS idx_document_versions_document
ON document_versions(document_id);

CREATE INDEX IF NOT EXISTS idx_document_versions_status
ON document_versions(status);

CREATE TABLE IF NOT EXISTS document_scopes (
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    aircraft TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(version_id, aircraft, operator)
);

CREATE INDEX IF NOT EXISTS idx_document_scopes_aircraft
ON document_scopes(aircraft);

CREATE TABLE IF NOT EXISTS document_sections (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES document_sections(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    hierarchy_path TEXT NOT NULL,
    pdf_page_start INTEGER,
    pdf_page_end INTEGER,
    printed_page_start TEXT,
    printed_page_end TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_sections_version
ON document_sections(version_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_document_sections_parent
ON document_sections(parent_id);

CREATE TABLE IF NOT EXISTS source_chunks (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES document_sections(id) ON DELETE SET NULL,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL,
    hierarchy_path TEXT NOT NULL,
    content TEXT NOT NULL,
    pdf_pages_json TEXT NOT NULL DEFAULT '[]',
    printed_pages_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    token_count INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_source_chunks_version
ON source_chunks(version_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_source_chunks_section
ON source_chunks(section_id);

CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks_fts USING fts5(
    heading,
    hierarchy_path,
    content,
    content='source_chunks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS source_chunks_ai
AFTER INSERT ON source_chunks BEGIN
    INSERT INTO source_chunks_fts(rowid, heading, hierarchy_path, content)
    VALUES (new.rowid, new.heading, new.hierarchy_path, new.content);
END;

CREATE TRIGGER IF NOT EXISTS source_chunks_ad
AFTER DELETE ON source_chunks BEGIN
    INSERT INTO source_chunks_fts(source_chunks_fts, rowid, heading, hierarchy_path, content)
    VALUES ('delete', old.rowid, old.heading, old.hierarchy_path, old.content);
END;

CREATE TRIGGER IF NOT EXISTS source_chunks_au
AFTER UPDATE ON source_chunks BEGIN
    INSERT INTO source_chunks_fts(source_chunks_fts, rowid, heading, hierarchy_path, content)
    VALUES ('delete', old.rowid, old.heading, old.hierarchy_path, old.content);
    INSERT INTO source_chunks_fts(rowid, heading, hierarchy_path, content)
    VALUES (new.rowid, new.heading, new.hierarchy_path, new.content);
END;

CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    aircraft TEXT NOT NULL,
    ata_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(aircraft, slug)
);

CREATE INDEX IF NOT EXISTS idx_topics_aircraft
ON topics(aircraft);

CREATE TABLE IF NOT EXISTS topic_aliases (
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_topic_aliases_normalized
ON topic_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS topic_candidates (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES document_sections(id) ON DELETE SET NULL,
    proposed_topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    hierarchy_path TEXT NOT NULL,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK(status IN ('candidate', 'accepted', 'rejected', 'ambiguous')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_candidates_version
ON topic_candidates(version_id, status);

CREATE TABLE IF NOT EXISTS topic_searches (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    search_type TEXT NOT NULL,
    query_fingerprint TEXT NOT NULL,
    searched_aliases_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL
        CHECK(status IN ('found', 'not_found', 'not_applicable', 'ambiguous', 'not_searched')),
    highest_score REAL,
    processor_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(topic_id, version_id, search_type, query_fingerprint, processor_version)
);

CREATE INDEX IF NOT EXISTS idx_topic_searches_lookup
ON topic_searches(topic_id, version_id, search_type);

CREATE TABLE IF NOT EXISTS topic_evidence (
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES source_chunks(id) ON DELETE CASCADE,
    search_id TEXT REFERENCES topic_searches(id) ON DELETE SET NULL,
    evidence_role TEXT NOT NULL,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    created_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, chunk_id, evidence_role)
);

CREATE INDEX IF NOT EXISTS idx_topic_evidence_chunk
ON topic_evidence(chunk_id);

CREATE TABLE IF NOT EXISTS evidence_reviews (
    topic_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    classification TEXT NOT NULL
        CHECK(classification IN (
            'procedure_candidate',
            'supporting_reference',
            'incidental',
            'manual_review'
        )),
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    processor_version TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK(review_status IN ('needs_review', 'approved', 'rejected')),
    reviewer TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, chunk_id, evidence_role),
    FOREIGN KEY(topic_id, chunk_id, evidence_role)
        REFERENCES topic_evidence(topic_id, chunk_id, evidence_role)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_evidence_reviews_queue
ON evidence_reviews(review_status, classification, score DESC);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    claim_text TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_topic
ON claims(topic_id);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES source_chunks(id) ON DELETE CASCADE,
    evidence_quote TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(claim_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS claim_metadata (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL
        CHECK(section_key IN (
            'overview',
            'system_flow',
            'components',
            'control_logic',
            'maintenance_context',
            'applicability'
        )),
    sort_order INTEGER NOT NULL DEFAULT 0,
    applicability TEXT,
    reviewer TEXT,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_claim_metadata_section
ON claim_metadata(section_key, sort_order);

CREATE TABLE IF NOT EXISTS source_conflicts (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    disposition TEXT,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_evidence (
    conflict_id TEXT NOT NULL REFERENCES source_conflicts(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES source_chunks(id) ON DELETE CASCADE,
    observed_value TEXT,
    PRIMARY KEY(conflict_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    input_type TEXT NOT NULL,
    input_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    processor_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(job_type, input_type, input_id, input_hash, processor_version)
);

CREATE INDEX IF NOT EXISTS idx_processing_jobs_status
ON processing_jobs(status, job_type);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    topic_id TEXT REFERENCES topics(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    created_at TEXT NOT NULL,
    UNIQUE(artifact_type, artifact_path, content_hash)
);

CREATE TABLE IF NOT EXISTS artifact_reviews (
    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE CASCADE,
    review_status TEXT NOT NULL
        CHECK(review_status IN ('approved', 'rejected')),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);
