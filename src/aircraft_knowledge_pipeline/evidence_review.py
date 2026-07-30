from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from .store import KnowledgeStore, normalize_alias


EVIDENCE_REVIEW_VERSION = "evidence-review-2"
PROCEDURE_SIGNALS = (
    "condition",
    "may be inoperative",
    "provided",
    "provided ",
    "repair category",
    "no. required for dispatch",
    "switch",
    "selector",
)
INCIDENTAL_SIGNALS = (
    "intentionally blank",
    "table of contents",
    "highlights of change",
    "list of effective pages",
)


@dataclass(frozen=True)
class EvidenceTriageResult:
    topic_id: str
    chunk_id: str
    evidence_role: str
    classification: str
    score: float
    reasons: tuple[str, ...]
    stored: bool


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def classify_evidence(
    *,
    topic_title: str,
    document_type: str,
    heading: str,
    hierarchy_path: str,
    content: str,
) -> tuple[str, float, tuple[str, ...]]:
    topic = normalize_alias(topic_title)
    searchable_heading = normalize_alias(f"{heading} {hierarchy_path}")
    searchable_content = normalize_alias(content)
    topic_tokens = list(dict.fromkeys(_tokens(topic_title)))
    combined = f"{searchable_heading} {searchable_content}"
    covered = sum(token in combined.split() for token in topic_tokens)
    coverage = covered / max(len(topic_tokens), 1)
    reasons: list[str] = [f"topic token coverage {covered}/{len(topic_tokens)}"]
    score = 0.15 + 0.35 * coverage

    phrase_in_heading = topic in searchable_heading
    phrase_in_content = topic in searchable_content
    if phrase_in_heading:
        score += 0.25
        reasons.append("canonical phrase appears in heading or hierarchy")
    if phrase_in_content:
        score += 0.15
        reasons.append("canonical phrase appears in page text")

    normalized_content = normalize_alias(content)
    incidental = next(
        (signal for signal in INCIDENTAL_SIGNALS if signal in normalized_content),
        None,
    )
    if incidental:
        score = min(score, 0.2)
        reasons.append(f"incidental-page signal: {incidental}")
        return "incidental", round(score, 3), tuple(reasons)

    procedure_signal = next(
        (signal for signal in PROCEDURE_SIGNALS if signal in normalized_content),
        None,
    )
    if document_type in {"mel", "qrh"} and procedure_signal:
        score += 0.2
        reasons.append(f"procedure signal: {procedure_signal}")
        classification = "procedure_candidate"
    elif document_type == "fcom" and coverage >= 0.75:
        reasons.append("FCOM system-description context")
        classification = "supporting_reference"
    elif coverage >= 0.75 and (phrase_in_heading or phrase_in_content):
        classification = "supporting_reference"
    else:
        classification = "manual_review"

    if len(topic_tokens) == 1:
        score -= 0.2
        reasons.append("single-word topic is prone to incidental matches")
        if not phrase_in_heading and classification != "procedure_candidate":
            classification = "manual_review"
    return classification, round(max(0.0, min(score, 1.0)), 3), tuple(reasons)


def triage_evidence(
    store: KnowledgeStore,
    *,
    topic_ids: Sequence[str] = (),
    force: bool = False,
) -> list[EvidenceTriageResult]:
    results: list[EvidenceTriageResult] = []
    for row in store.evidence_rows_for_review(topic_ids=topic_ids):
        classification, score, reasons = classify_evidence(
            topic_title=str(row["topic_title"]),
            document_type=str(row["document_type"]),
            heading=str(row["heading"]),
            hierarchy_path=str(row["hierarchy_path"]),
            content=str(row["content"]),
        )
        stored = store.upsert_evidence_review(
            topic_id=str(row["topic_id"]),
            chunk_id=str(row["chunk_id"]),
            evidence_role=str(row["evidence_role"]),
            classification=classification,
            score=score,
            reasons=reasons,
            processor_version=EVIDENCE_REVIEW_VERSION,
            force=force,
        )
        results.append(
            EvidenceTriageResult(
                topic_id=str(row["topic_id"]),
                chunk_id=str(row["chunk_id"]),
                evidence_role=str(row["evidence_role"]),
                classification=classification,
                score=score,
                reasons=reasons,
                stored=stored,
            )
        )
    return results


def render_review_rows(rows: Sequence[object]) -> list[dict[str, object]]:
    rendered: list[dict[str, object]] = []
    for row in rows:
        rendered.append(
            {
                "topic_id": row["topic_id"],
                "topic_title": row["topic_title"],
                "chunk_id": row["chunk_id"],
                "evidence_role": row["evidence_role"],
                "document_type": row["document_type"],
                "classification": row["classification"],
                "score": row["score"],
                "reasons": json.loads(row["reasons_json"]),
                "review_status": row["review_status"],
                "heading": row["heading"],
                "hierarchy_path": row["hierarchy_path"],
                "pdf_pages": json.loads(row["pdf_pages_json"]),
                "printed_pages": json.loads(row["printed_pages_json"]),
                "excerpt": row["excerpt"],
            }
        )
    return rendered
