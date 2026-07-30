from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Sequence

from .store import KnowledgeStore, SearchResult, normalize_alias, sha256_text
from .source_policy import CORE_SOURCE_TYPES


TOPIC_ENRICHMENT_VERSION = "topic-enrichment-1"
DEFAULT_ENRICHMENT_TYPES = CORE_SOURCE_TYPES


@dataclass(frozen=True)
class TopicEnrichmentResult:
    topic_id: str
    version_id: str
    document_type: str
    status: str
    evidence_count: int = 0


def enrich_topics(
    store: KnowledgeStore,
    *,
    topic_ids: Sequence[str] = (),
    document_types: Sequence[str] = DEFAULT_ENRICHMENT_TYPES,
    max_evidence_per_document: int = 3,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[TopicEnrichmentResult]:
    if max_evidence_per_document < 1 or max_evidence_per_document > 20:
        raise ValueError("Evidence limit must be between 1 and 20.")
    topics = store.canonical_topic_rows(topic_ids=topic_ids)
    if topic_ids and len(topics) != len(set(topic_ids)):
        found = {str(row["id"]) for row in topics}
        missing = sorted(set(topic_ids) - found)
        raise ValueError("Unknown topic IDs: " + ", ".join(missing))
    versions = store.enrichment_source_versions(document_types=document_types)
    results: list[TopicEnrichmentResult] = []

    for topic in topics:
        topic_id = str(topic["id"])
        aliases = store.aliases_for_topic(topic_id) or [str(topic["title"])]
        normalized_aliases = sorted(
            dict.fromkeys(normalize_alias(alias) for alias in aliases)
        )
        for version in versions:
            version_id = str(version["version_id"])
            document_type = str(version["document_type"])
            search_type = f"{document_type}-enrichment"
            fingerprint = sha256_text(
                json.dumps(
                    {
                        "aliases": normalized_aliases,
                        "document_checksum": str(version["checksum"]),
                        "limit": max_evidence_per_document,
                    },
                    sort_keys=True,
                )
            )
            existing_search = store.topic_search_id(
                topic_id=topic_id,
                version_id=version_id,
                search_type=search_type,
                query_fingerprint=fingerprint,
                processor_version=TOPIC_ENRICHMENT_VERSION,
            )
            if existing_search and not force:
                results.append(
                    TopicEnrichmentResult(
                        topic_id=topic_id,
                        version_id=version_id,
                        document_type=document_type,
                        status="skipped",
                    )
                )
                continue

            if progress:
                progress(f"Searching {document_type} for {topic['title']}...")
            matches_by_chunk: dict[str, SearchResult] = {}
            for alias in aliases:
                for match in store.search_chunks(
                    alias,
                    version_id=version_id,
                    limit=max_evidence_per_document,
                ):
                    matches_by_chunk.setdefault(match.chunk_id, match)
            matches = sorted(
                matches_by_chunk.values(),
                key=lambda match: match.rank,
            )[:max_evidence_per_document]
            status = "found" if matches else "not_found"
            evidence_role = f"{document_type}_support"
            store.clear_topic_evidence_for_version(
                topic_id=topic_id,
                version_id=version_id,
                evidence_role=evidence_role,
            )
            search_id = store.record_topic_search(
                topic_id=topic_id,
                version_id=version_id,
                search_type=search_type,
                query_fingerprint=fingerprint,
                status=status,
                processor_version=TOPIC_ENRICHMENT_VERSION,
                searched_aliases=aliases,
                highest_score=max((-match.rank for match in matches), default=None),
            )
            for match in matches:
                store.add_topic_evidence(
                    topic_id=topic_id,
                    chunk_id=match.chunk_id,
                    search_id=search_id,
                    evidence_role=evidence_role,
                )
            results.append(
                TopicEnrichmentResult(
                    topic_id=topic_id,
                    version_id=version_id,
                    document_type=document_type,
                    status=status,
                    evidence_count=len(matches),
                )
            )
    return results
