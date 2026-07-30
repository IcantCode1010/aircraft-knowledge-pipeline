from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from .research_packet import _clean_text
from .store import KnowledgeStore, sha256_text


OKF_PREVIEW_SCHEMA = "akp-okf-preview/v1"
DEFAULT_OKF_PREVIEW_ROOT = Path("output/okf-previews")

SECTION_LABELS = {
    "overview": "Overview",
    "system_flow": "How the system works",
    "components": "Components",
    "control_logic": "Control logic",
    "maintenance_context": "Maintenance context",
    "applicability": "Applicability",
}

SECTION_INTRODUCTIONS = {
    "overview": "What the system does and why it is installed.",
    "system_flow": "The normal airflow path, described without procedural instructions.",
    "components": "The principal equipment identified by the approved source material.",
    "control_logic": "A high-level explanation of the inputs that govern system operation.",
    "maintenance_context": (
        "Source references for maintenance awareness. This section does not reproduce "
        "or replace an approved maintenance procedure."
    ),
    "applicability": (
        "Configuration boundaries that must be resolved before this material can be "
        "published as aircraft-specific guidance."
    ),
}


@dataclass(frozen=True)
class OkfPreviewResult:
    topic_id: str
    title: str
    path: str
    claim_count: int
    approved_claim_count: int
    content_hash: str


def _reference(row: object) -> str:
    pdf_pages = json.loads(row["pdf_pages_json"])
    printed_pages = json.loads(row["printed_pages_json"])
    parts = [f"PDF page {page}" for page in pdf_pages]
    if printed_pages:
        parts.append("printed " + ", ".join(printed_pages))
    return "; ".join(parts) or "page unavailable"


def build_okf_preview(
    store: KnowledgeStore,
    *,
    topic_id: str,
    output_root: str | Path = DEFAULT_OKF_PREVIEW_ROOT,
) -> OkfPreviewResult:
    topics = store.canonical_topic_rows(topic_ids=[topic_id])
    if not topics:
        raise ValueError(f"Unknown topic ID: {topic_id}")
    rows = store.content_claim_rows(topic_id)
    if not rows:
        raise ValueError(f"Topic has no structured content claims: {topic_id}")

    topic = topics[0]
    claims: dict[str, dict[str, object]] = {}
    for row in rows:
        claim_id = str(row["claim_id"])
        claim = claims.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                "claim_text": str(row["claim_text"]),
                "review_status": str(row["review_status"]),
                "section_key": str(row["section_key"]),
                "sort_order": int(row["sort_order"]),
                "applicability": (
                    str(row["applicability"]) if row["applicability"] else ""
                ),
                "evidence": [],
            },
        )
        claim["evidence"].append(row)

    ordered_claims = list(claims.values())
    approved_count = sum(
        claim["review_status"] == "approved" for claim in ordered_claims
    )
    section_claims: dict[str, list[dict[str, object]]] = {}
    for claim in ordered_claims:
        section_claims.setdefault(str(claim["section_key"]), []).append(claim)

    lines = [
        "---",
        f"schema: {OKF_PREVIEW_SCHEMA}",
        f"topic_id: {topic_id}",
        f"title: {json.dumps(_clean_text(str(topic['title'])))}",
        f"aircraft: {json.dumps(_clean_text(str(topic['aircraft'])))}",
        f"ata: {json.dumps(json.loads(topic['ata_json']))}",
        'source_profile: "core"',
        'included_source_types: ["training", "amm"]',
        "structure_status: needs_review",
        (
            "content_status: approved"
            if approved_count == len(ordered_claims)
            else "content_status: needs_review"
        ),
        f"claim_count: {len(ordered_claims)}",
        f"approved_claim_count: {approved_count}",
        "---",
        "",
        f"# {_clean_text(str(topic['title']))}",
        "",
        "> Content and structure preview. Not approved for operational or maintenance use.",
        "",
        "## At a glance",
        "",
        f"- Aircraft family: {_clean_text(str(topic['aircraft']))}",
        f"- ATA chapter: {', '.join(json.loads(topic['ata_json']))}",
        "- Core sources: Training and AMM",
        (
            f"- Content maturity: {approved_count} of {len(ordered_claims)} "
            "claims approved"
        ),
        "",
    ]

    short_ids: dict[str, str] = {}
    for index, claim in enumerate(ordered_claims, start=1):
        short_ids[str(claim["claim_id"])] = f"C{index}"

    for section_key in SECTION_LABELS:
        lines.extend(
            [
                f"## {SECTION_LABELS[section_key]}",
                "",
                SECTION_INTRODUCTIONS[section_key],
                "",
            ]
        )
        current_claims = section_claims.get(section_key, [])
        if not current_claims:
            lines.extend(
                [
                    "_No evidence-supported content has been drafted for this section._",
                    "",
                ]
            )
            continue
        for claim in current_claims:
            short_id = short_ids[str(claim["claim_id"])]
            status = str(claim["review_status"]).replace("_", " ")
            lines.extend(
                [
                    f"{_clean_text(str(claim['claim_text']))} [{short_id}]",
                    "",
                    f"_Claim status: {status}._",
                ]
            )
            if claim["applicability"]:
                lines.append(
                    f"_Applicability: {_clean_text(str(claim['applicability']))}_"
                )
            lines.append("")

    lines.extend(
        [
            "## Evidence map",
            "",
            "| Claim | Source | Reference | Evidence ID |",
            "|---|---|---|---|",
        ]
    )
    for claim in ordered_claims:
        short_id = short_ids[str(claim["claim_id"])]
        for row in claim["evidence"]:
            source = (
                f"{str(row['document_type']).upper()}: "
                f"{_clean_text(str(row['heading']))}"
            )
            lines.append(
                f"| {short_id} | {source} | {_reference(row)} | "
                f"`{row['chunk_id']}` |"
            )

    lines.extend(
        [
            "",
            "## Approval checklist",
            "",
            "- [ ] The section order supports the intended reader.",
            "- [ ] Every statement is concise, clear, and technically accurate.",
            "- [ ] Configuration-dependent statements are labeled correctly.",
            "- [ ] Maintenance context does not become an unofficial procedure.",
            "- [ ] The evidence map is sufficient for technical review.",
            "- [ ] The content form is approved before TypeScript implementation.",
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
                f"akp:okf-preview:{topic_id}:{content_hash}",
            )
        ),
        topic_id=topic_id,
        artifact_type="okf_preview",
        artifact_path=str(output_path.resolve()),
        content_hash=content_hash,
        schema_version=OKF_PREVIEW_SCHEMA,
    )
    return OkfPreviewResult(
        topic_id=topic_id,
        title=str(topic["title"]),
        path=str(output_path.resolve()),
        claim_count=len(ordered_claims),
        approved_claim_count=approved_count,
        content_hash=content_hash,
    )
