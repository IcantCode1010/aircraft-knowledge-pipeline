from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Sequence

from .store import KnowledgeStore, TopicCandidate, normalize_alias, sha256_text


TOPIC_DISCOVERY_VERSION = "topic-discovery-1"
DEFAULT_TOPIC_SOURCE_TYPES = ("training",)
SUBJECT_PATTERN = re.compile(
    r"^subject\s+(?P<ata>\d{2}(?:-\d{2}){1,2})\s*[-–—:]\s*(?P<title>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TopicDiscoveryResult:
    document_id: str
    version_id: str
    document_type: str
    status: str
    candidate_count: int = 0
    error: str | None = None


def clean_candidate_title(value: str) -> str:
    return " ".join(value.replace("\x11", " ").split()).strip(" -–—:")


def candidate_from_section(
    *,
    version_id: str,
    section_id: str,
    title: str,
    hierarchy_path: str,
) -> TopicCandidate | None:
    match = SUBJECT_PATTERN.match(clean_candidate_title(title))
    if not match:
        return None
    candidate_title = clean_candidate_title(match.group("title"))
    if len(normalize_alias(candidate_title)) < 4:
        return None
    stable_key = f"{version_id}:{normalize_alias(candidate_title)}"
    return TopicCandidate(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"akp:topic-candidate:{stable_key}")),
        section_id=section_id,
        title=candidate_title,
        hierarchy_path=hierarchy_path,
        confidence=0.98,
    )


def discover_version_topics(
    store: KnowledgeStore,
    *,
    version_id: str,
) -> list[TopicCandidate]:
    candidates_by_title: dict[str, TopicCandidate] = {}
    for section in store.sections_for_version(version_id):
        candidate = candidate_from_section(
            version_id=version_id,
            section_id=str(section["id"]),
            title=str(section["title"]),
            hierarchy_path=str(section["hierarchy_path"]),
        )
        if candidate is None:
            continue
        candidates_by_title.setdefault(normalize_alias(candidate.title), candidate)
    return list(candidates_by_title.values())


def discover_topic_candidates(
    store: KnowledgeStore,
    *,
    document_types: Sequence[str] = DEFAULT_TOPIC_SOURCE_TYPES,
    document_id: str | None = None,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[TopicDiscoveryResult]:
    normalized_types = tuple(
        dict.fromkeys(value.strip().lower() for value in document_types if value.strip())
    )
    versions = store.topic_source_versions(
        document_types=normalized_types,
        document_id=document_id,
    )
    results: list[TopicDiscoveryResult] = []

    for version in versions:
        version_id = str(version["version_id"])
        document_id_value = str(version["document_id"])
        document_type = str(version["document_type"])
        input_hash = sha256_text(
            json.dumps(
                {
                    "checksum": str(version["checksum"]),
                    "document_type": document_type,
                    "rule": "subject-ata-heading",
                },
                sort_keys=True,
            )
        )
        job_id, created = store.create_processing_job(
            job_type="discover_topics",
            input_type="document_version",
            input_id=version_id,
            input_hash=input_hash,
            processor_version=TOPIC_DISCOVERY_VERSION,
        )
        if not created and store.processing_job_status(job_id) == "completed" and not force:
            results.append(
                TopicDiscoveryResult(
                    document_id=document_id_value,
                    version_id=version_id,
                    document_type=document_type,
                    status="skipped",
                )
            )
            continue

        if force:
            store.reset_processing_job(job_id)
        store.start_processing_job(job_id)
        if progress:
            progress(f"Discovering topics in {document_id_value}...")
        try:
            candidates = discover_version_topics(store, version_id=version_id)
            store.replace_topic_candidates(
                version_id=version_id,
                candidates=candidates,
            )
            store.complete_processing_job(job_id)
            results.append(
                TopicDiscoveryResult(
                    document_id=document_id_value,
                    version_id=version_id,
                    document_type=document_type,
                    status="completed",
                    candidate_count=len(candidates),
                )
            )
        except Exception as error:  # keep batch processing other documents
            store.fail_processing_job(job_id, str(error))
            results.append(
                TopicDiscoveryResult(
                    document_id=document_id_value,
                    version_id=version_id,
                    document_type=document_type,
                    status="failed",
                    error=str(error),
                )
            )
    return results
