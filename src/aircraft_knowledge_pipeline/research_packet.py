from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from .store import KnowledgeStore, sha256_text
from .source_policy import SOURCE_PROFILES


RESEARCH_PACKET_SCHEMA = "akp-research-packet/v2"
DEFAULT_PACKET_ROOT = Path("output/research-packets")


@dataclass(frozen=True)
class ResearchPacketResult:
    topic_id: str
    title: str
    path: str
    source_profile: str
    approved_evidence_count: int
    excluded_approved_evidence_count: int
    content_hash: str


def _clean_text(value: str) -> str:
    value = value.translate(
        str.maketrans(
            {
                "\u00a9": "(c)",
                "\u00b0": " degrees ",
                "\u2011": "-",
                "\u2013": "-",
                "\u2014": "-",
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
            }
        )
    )
    value = "".join(character if ord(character) >= 32 or character in "\n\t" else " " for character in value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[ \t]+", " ", value).strip()


def _excerpt(value: str, limit: int = 700) -> str:
    compact = re.sub(r"\s+", " ", _clean_text(value))
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _reference(row: object) -> str:
    pdf_pages = json.loads(row["pdf_pages_json"])
    printed_pages = json.loads(row["printed_pages_json"])
    pieces = [f"PDF page {page}" for page in pdf_pages]
    if printed_pages:
        pieces.append("printed " + ", ".join(printed_pages))
    return "; ".join(pieces) or "page unavailable"


def build_research_packet(
    store: KnowledgeStore,
    *,
    topic_id: str,
    output_root: str | Path = DEFAULT_PACKET_ROOT,
    source_profile: str = "core",
) -> ResearchPacketResult:
    if source_profile not in SOURCE_PROFILES:
        raise ValueError(f"Unknown research packet source profile: {source_profile}")
    topics = store.canonical_topic_rows(topic_ids=[topic_id])
    if not topics:
        raise ValueError(f"Unknown topic ID: {topic_id}")
    included_types = SOURCE_PROFILES[source_profile]
    all_approved_rows = store.approved_evidence_for_topic(topic_id)
    rows = store.approved_evidence_for_topic(
        topic_id,
        document_types=included_types,
    )
    if not rows:
        raise ValueError(
            f"Topic has no approved evidence for the {source_profile} source profile "
            f"and cannot produce a packet: {topic_id}"
        )
    topic = topics[0]
    summary = store.evidence_review_summary(topic_id)
    reviewed_through = max(str(row["reviewed_at"]) for row in rows)
    by_type: dict[str, list[object]] = {}
    for row in rows:
        by_type.setdefault(str(row["document_type"]), []).append(row)

    lines = [
        "---",
        f"schema: {RESEARCH_PACKET_SCHEMA}",
        f"topic_id: {topic_id}",
        f"title: {json.dumps(_clean_text(str(topic['title'])))}",
        f"aircraft: {json.dumps(_clean_text(str(topic['aircraft'])))}",
        f"ata: {json.dumps(json.loads(topic['ata_json']))}",
        f"source_profile: {source_profile}",
        f"included_source_types: {json.dumps(list(included_types) or ['all-approved'])}",
        "review_status: needs_review",
        f"evidence_reviewed_through: {reviewed_through}",
        f"approved_evidence_count: {len(rows)}",
        "---",
        "",
        f"# {_clean_text(str(topic['title']))}",
        "",
        "> Research packet only. This is not an approved operational or maintenance instruction.",
        "",
        "## Review summary",
        "",
        f"- Approved evidence in this packet: {len(rows)}",
        f"- Other approved evidence excluded by source policy: {len(all_approved_rows) - len(rows)}",
        f"- Rejected evidence: {summary.get('rejected', 0)}",
        f"- Awaiting review: {summary.get('needs_review', 0)}",
        "",
        "## Approved evidence",
        "",
    ]
    source_labels = {
        "training": "Training and visual system references",
        "fcom": "Flight crew system and operating references",
        "amm": "Maintenance references",
        "mel": "Dispatch relief references",
        "qrh": "Operational procedure references",
    }
    for document_type, evidence_rows in by_type.items():
        lines.extend(
            [
                f"### {source_labels.get(document_type, document_type.upper())}",
                "",
            ]
        )
        for index, row in enumerate(evidence_rows, start=1):
            lines.extend(
                [
                    f"#### {document_type.upper()}-{index}: {_clean_text(str(row['heading']))}",
                    "",
                    f"- Source: {_clean_text(str(row['document_title']))} (`{_clean_text(str(row['file_name']))}`)",
                    f"- Reference: {_reference(row)}",
                    f"- Hierarchy: {_clean_text(str(row['hierarchy_path']))}",
                    f"- Classification: {row['classification']}",
                    f"- Reviewed by: {_clean_text(str(row['reviewer']))}",
                    f"- Chunk ID: `{row['chunk_id']}`",
                    "",
                    f"Evidence excerpt: {_excerpt(str(row['content']))}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Synthesis checklist",
            "",
            "- [ ] Confirm effectivity and applicability for every retained statement.",
            "- [ ] Separate system description from maintenance, dispatch, and flight-crew procedures.",
            "- [ ] Resolve duplicate or conflicting source statements.",
            "- [ ] Convert evidence into concise claims with page citations.",
            "- [ ] Obtain final technical review before OKF publication.",
            "",
        ]
    )
    content = "\n".join(lines)
    output_path = Path(output_root) / f"{topic_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8", newline="\n")
    content_hash = sha256_text(content)
    store.record_artifact(
        artifact_id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"akp:research-packet:{topic_id}:{content_hash}",
            )
        ),
        topic_id=topic_id,
        artifact_type="research_packet",
        artifact_path=str(output_path.resolve()),
        content_hash=content_hash,
        schema_version=RESEARCH_PACKET_SCHEMA,
    )
    return ResearchPacketResult(
        topic_id=topic_id,
        title=str(topic["title"]),
        path=str(output_path.resolve()),
        source_profile=source_profile,
        approved_evidence_count=len(rows),
        excluded_approved_evidence_count=len(all_approved_rows) - len(rows),
        content_hash=content_hash,
    )
