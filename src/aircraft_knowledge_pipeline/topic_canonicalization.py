from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .store import KnowledgeStore, normalize_alias


ATA_PATTERN = re.compile(r"\b(?:chapter|section|subject)\s+(\d{2})(?:-\d{2})?", re.I)


@dataclass(frozen=True)
class TopicPromotionResult:
    candidate_id: str
    topic_id: str
    title: str
    slug: str
    ata: tuple[str, ...]


def slugify(value: str) -> str:
    return normalize_alias(value).replace(" ", "-")


def aircraft_key(value: str) -> str:
    return normalize_alias(value).replace(" ", "")


def ata_from_hierarchy(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ATA_PATTERN.findall(value)))


def promote_topic_candidates(
    store: KnowledgeStore,
    *,
    aircraft: str,
    candidate_ids: Sequence[str] = (),
    accept_all: bool = False,
    minimum_confidence: float = 0.98,
) -> list[TopicPromotionResult]:
    if not accept_all and not candidate_ids:
        raise ValueError("Specify candidate IDs or explicitly use accept_all.")
    rows = store.candidate_rows(
        candidate_ids=candidate_ids,
        minimum_confidence=minimum_confidence,
    )
    requested = set(candidate_ids)
    found = {str(row["id"]) for row in rows}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(
            "Candidate IDs were not found or were below the confidence threshold: "
            + ", ".join(missing)
        )

    results: list[TopicPromotionResult] = []
    for row in rows:
        title = str(row["title"])
        slug = slugify(title)
        topic_id = f"{aircraft_key(aircraft)}-{slug}"
        ata = ata_from_hierarchy(str(row["hierarchy_path"]))
        store.create_topic(
            topic_id=topic_id,
            slug=slug,
            title=title,
            aircraft=aircraft,
            ata=ata,
            review_status="needs_review",
        )
        store.add_topic_aliases(topic_id, [title])
        store.accept_topic_candidate(str(row["id"]), topic_id)
        results.append(
            TopicPromotionResult(
                candidate_id=str(row["id"]),
                topic_id=topic_id,
                title=title,
                slug=slug,
                ata=ata,
            )
        )
    return results
