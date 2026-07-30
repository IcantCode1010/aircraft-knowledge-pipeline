from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from .okf_preview import SECTION_LABELS
from .research_packet import _clean_text
from .store import KnowledgeStore, sha256_text


EDITORIAL_DRAFT_SCHEMA = "akp-editorial-draft/v2"
DEFAULT_EDITORIAL_DRAFT_ROOT = Path("output/editorial-drafts")


@dataclass(frozen=True)
class EditorialDraftResult:
    topic_id: str
    title: str
    path: str
    content_model_hash: str
    claim_count: int
    source_count: int
    content_hash: str


def _reference(row: object) -> str:
    pdf_pages = json.loads(row["pdf_pages_json"])
    printed_pages = json.loads(row["printed_pages_json"])
    parts = [f"PDF page {page}" for page in pdf_pages]
    if printed_pages:
        parts.append("printed " + ", ".join(printed_pages))
    return "; ".join(parts) or "page unavailable"


def build_editorial_draft(
    store: KnowledgeStore,
    *,
    topic_id: str,
    output_root: str | Path = DEFAULT_EDITORIAL_DRAFT_ROOT,
) -> EditorialDraftResult:
    topics = store.canonical_topic_rows(topic_ids=[topic_id])
    if not topics:
        raise ValueError(f"Unknown topic ID: {topic_id}")
    approved_model = store.approved_artifact(
        topic_id=topic_id,
        artifact_type="okf_preview",
    )
    if not approved_model:
        raise ValueError(
            "The latest OKF content model must be approved before an editorial "
            f"draft can be built: {topic_id}"
        )
    rows = store.content_claim_rows(topic_id)
    if not rows:
        raise ValueError(f"Topic has no structured content claims: {topic_id}")

    topic = topics[0]
    claims: dict[str, dict[str, object]] = {}
    sources: dict[str, dict[str, object]] = {}
    for row in rows:
        claim_id = str(row["claim_id"])
        claim = claims.setdefault(
            claim_id,
            {
                "claim_text": _clean_text(str(row["claim_text"])),
                "section_key": str(row["section_key"]),
                "sort_order": int(row["sort_order"]),
                "applicability": (
                    _clean_text(str(row["applicability"]))
                    if row["applicability"]
                    else ""
                ),
                "source_keys": [],
            },
        )
        source_key = str(row["chunk_id"])
        if source_key not in sources:
            sources[source_key] = {
                "number": len(sources) + 1,
                "document_type": str(row["document_type"]),
                "heading": _clean_text(str(row["heading"])),
                "reference": _reference(row),
            }
        claim["source_keys"].append(source_key)

    ordered_claims = list(claims.values())
    by_section: dict[str, list[dict[str, object]]] = {}
    for claim in ordered_claims:
        by_section.setdefault(str(claim["section_key"]), []).append(claim)

    def cited_text(claim: dict[str, object]) -> str:
        source_keys = list(dict.fromkeys(claim["source_keys"]))
        citations = "".join(
            f"[^{sources[source_key]['number']}]" for source_key in source_keys
        )
        return f"{claim['claim_text']}{citations}"

    lines = [
        "---",
        f"schema: {EDITORIAL_DRAFT_SCHEMA}",
        f"topic_id: {topic_id}",
        f"title: {json.dumps(_clean_text(str(topic['title'])))}",
        f"aircraft: {json.dumps(_clean_text(str(topic['aircraft'])))}",
        f"ata: {json.dumps(json.loads(topic['ata_json']))}",
        'source_profile: "core"',
        f"based_on_content_model_hash: {approved_model['content_hash']}",
        "content_model_status: approved",
        "editorial_status: needs_review",
        "---",
        "",
        f"# {_clean_text(str(topic['title']))}",
        "",
        "> Editorial review draft. Not approved for operational or maintenance use.",
        "",
        "## Overview",
        "",
        " ".join(cited_text(claim) for claim in by_section.get("overview", [])),
        "",
        "## How the system works",
        "",
    ]

    for claim in by_section.get("system_flow", []):
        lines.extend([cited_text(claim), ""])

    lines.extend(["## Components", ""])
    for claim in by_section.get("components", []):
        lines.append(f"- {cited_text(claim)}")
    lines.append("")

    lines.extend(["## Control logic", ""])
    for claim in by_section.get("control_logic", []):
        lines.extend([cited_text(claim), ""])

    lines.extend(
        [
            "## Maintenance context",
            "",
            "> Training overview only. This summary does not replace the current "
            "approved AMM, required tooling, safety precautions, or effectivity checks.",
            "",
            "At a high level, maintenance of the recirculation system includes:",
            "",
        ]
    )
    for claim in by_section.get("maintenance_context", []):
        lines.append(f"- {cited_text(claim)}")
    lines.append("")

    lines.extend(
        [
            "## Applicability",
            "",
        ]
    )
    for claim in by_section.get("applicability", []):
        lines.extend([f"**Configuration note:** {cited_text(claim)}", ""])

    lines.extend(["## Sources", ""])
    for source in sources.values():
        lines.append(
            f"[^{source['number']}]: {source['document_type'].upper()}, "
            f"{source['heading']}, {source['reference']}."
        )

    lines.extend(
        [
            "",
            "## Editorial approval checklist",
            "",
            "- [ ] The wording is clear for the intended reader.",
            "- [ ] The technical meaning matches the approved content model.",
            "- [ ] The citations support the statements they follow.",
            "- [ ] Configuration limits are prominent enough.",
            "- [ ] Maintenance language cannot be mistaken for a procedure.",
            "- [ ] The exact reader-facing copy is approved before TypeScript work.",
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
                f"akp:editorial-draft:{topic_id}:{content_hash}",
            )
        ),
        topic_id=topic_id,
        artifact_type="editorial_draft",
        artifact_path=str(output_path.resolve()),
        content_hash=content_hash,
        schema_version=EDITORIAL_DRAFT_SCHEMA,
    )
    return EditorialDraftResult(
        topic_id=topic_id,
        title=str(topic["title"]),
        path=str(output_path.resolve()),
        content_model_hash=str(approved_model["content_hash"]),
        claim_count=len(ordered_claims),
        source_count=len(sources),
        content_hash=content_hash,
    )
