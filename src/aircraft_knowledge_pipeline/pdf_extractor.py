from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from pypdf import PdfReader

from .store import (
    ExtractionChunk,
    ExtractionSection,
    KnowledgeStore,
    sha256_text,
)


PDF_EXTRACTOR_VERSION = "pdf-extractor-2"
MIN_USABLE_OUTLINE_ENTRIES = 10
DEFAULT_MAX_CHUNK_CHARACTERS = 6000


@dataclass(frozen=True)
class OutlineEntry:
    index: int
    parent_index: int | None
    level: int
    page_number: int | None
    title: str
    hierarchy: tuple[str, ...]


@dataclass(frozen=True)
class PdfExtraction:
    page_count: int
    outline_entry_count: int
    used_outline: bool
    blank_page_count: int
    sections: list[ExtractionSection]
    chunks: list[ExtractionChunk]


@dataclass(frozen=True)
class PdfExtractionResult:
    document_id: str
    version_id: str
    path: str
    status: str
    page_count: int = 0
    section_count: int = 0
    chunk_count: int = 0
    blank_page_count: int = 0
    used_outline: bool = False
    error: str | None = None


def extract_outline(reader: PdfReader) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []

    def walk(
        items: Sequence[object],
        *,
        parent_index: int | None = None,
        level: int = 0,
        parent_hierarchy: tuple[str, ...] = (),
    ) -> None:
        last_index: int | None = None
        last_hierarchy = parent_hierarchy
        for item in items:
            if isinstance(item, list):
                walk(
                    item,
                    parent_index=last_index if last_index is not None else parent_index,
                    level=level + 1,
                    parent_hierarchy=last_hierarchy,
                )
                continue

            title = str(getattr(item, "title", item)).strip() or "Untitled section"
            try:
                page_number = reader.get_destination_page_number(item) + 1
            except Exception:
                page_number = None

            index = len(entries)
            hierarchy = parent_hierarchy + (title,)
            entries.append(
                OutlineEntry(
                    index=index,
                    parent_index=parent_index,
                    level=level,
                    page_number=page_number,
                    title=title,
                    hierarchy=hierarchy,
                )
            )
            last_index = index
            last_hierarchy = hierarchy

    try:
        walk(reader.outline)
    except Exception:
        return []
    return entries


def normalize_extracted_text(value: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in value.replace("\x00", "").splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if lines and not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    return "\n".join(lines).strip()


def infer_page_heading(text: str, fallback: str) -> str:
    candidates: list[tuple[int, int, str]] = []
    for position, line in enumerate(text.splitlines()[:35]):
        stripped = line.strip()
        if not 4 <= len(stripped) <= 180:
            continue
        if re.fullmatch(r"[\d\s./-]+", stripped):
            continue
        if re.search(r"\b(?:copyright|proprietary|revision date|effective date)\b", stripped, re.I):
            continue
        if re.fullmatch(r"(?:page\s+)?\d+(?:\s+of\s+\d+)?", stripped, re.I):
            continue

        letters = [character for character in stripped if character.isalpha()]
        uppercase_ratio = (
            sum(character.isupper() for character in letters) / len(letters)
            if letters
            else 0.0
        )
        score = 0
        if uppercase_ratio >= 0.75:
            score += 4
        if re.search(
            r"\b(?:ATA|CHAPTER|SECTION|SUBJECT|PAGEBLOCK|PAGESET|TASK|SUBTASK|"
            r"NNC|MEL|MMEL)\b",
            stripped,
            re.I,
        ):
            score += 5
        if re.search(r"\b\d{2}-\d{2}(?:-\d{2})?\b", stripped):
            score += 4
        if stripped.endswith((".", ";", ":")):
            score -= 2
        score += max(0, 3 - position // 5)
        candidates.append((score, -position, stripped))

    if not candidates:
        return fallback
    best = max(candidates)
    return best[2]


def detect_printed_page(text: str) -> str | None:
    lines = text.splitlines()
    candidates = lines[:25] + lines[-15:]
    for position, line in enumerate(candidates):
        ata_match = re.fullmatch(r"\s*(\d{2}-\d{2}-\d{2})\s*", line)
        if not ata_match:
            continue
        nearby = candidates[max(0, position - 2) : position + 3]
        for nearby_line in nearby:
            page_match = re.search(r"\bPage\s+(\d+)\b", nearby_line, re.I)
            if page_match:
                return f"{ata_match.group(1)} Page {page_match.group(1)}"

    patterns = (
        r"\bPAGE\s+NO\.\s*\d{2}-\d+\b",
        r"\b\d{2}-\d{2}-\d{2}\s+Page\s+\d+\b",
        r"\b(?:NNC|NC|PI|CI)\s*[.\-]?\s*\d+(?:[.\-]\d+)+\b",
        r"\bPage\s+\d+(?:\s+of\s+\d+)?\b",
    )
    for pattern in patterns:
        for line in candidates:
            match = re.search(pattern, line, re.I)
            if match:
                return match.group(0)
    return None


def split_page_text(
    text: str,
    max_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[str]:
    if max_characters < 500:
        raise ValueError("Maximum chunk size must be at least 500 characters.")
    if len(text) <= max_characters:
        return [text] if text else []

    pieces: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.splitlines():
        if len(line) > max_characters:
            if current:
                pieces.append("\n".join(current).strip())
                current = []
                current_length = 0
            words = line.split()
            word_buffer: list[str] = []
            word_length = 0
            for word in words:
                proposed = word_length + len(word) + (1 if word_buffer else 0)
                if word_buffer and proposed > max_characters:
                    pieces.append(" ".join(word_buffer))
                    word_buffer = []
                    word_length = 0
                word_buffer.append(word)
                word_length += len(word) + (1 if len(word_buffer) > 1 else 0)
            if word_buffer:
                pieces.append(" ".join(word_buffer))
            continue

        proposed = current_length + len(line) + (1 if current else 0)
        if current and proposed > max_characters:
            pieces.append("\n".join(current).strip())
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + (1 if len(current) > 1 else 0)

    if current:
        pieces.append("\n".join(current).strip())
    return [piece for piece in pieces if piece]


def _section_id(version_id: str, identity: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"akp:{version_id}:section:{identity}"))


def _chunk_id(version_id: str, page_number: int, part: int, content_hash: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"akp:{version_id}:page:{page_number}:part:{part}:{content_hash}",
        )
    )


def build_outline_sections(
    *,
    entries: Sequence[OutlineEntry],
    version_id: str,
    document_title: str,
    page_count: int,
) -> tuple[list[ExtractionSection], dict[int, str]]:
    root_id = _section_id(version_id, "root")
    sections = [
        ExtractionSection(
            id=root_id,
            parent_id=None,
            ordinal=0,
            title=document_title,
            hierarchy_path=document_title,
            pdf_page_start=1 if page_count else None,
            pdf_page_end=page_count if page_count else None,
        )
    ]
    valid = [
        entry
        for entry in entries
        if entry.page_number is not None and 1 <= entry.page_number <= page_count
    ]
    valid_sorted = sorted(valid, key=lambda entry: (entry.page_number or 0, entry.index))
    section_id_by_outline_index = {
        entry.index: _section_id(
            version_id,
            f"outline:{entry.index}:{entry.page_number}:{' > '.join(entry.hierarchy)}",
        )
        for entry in valid_sorted
    }
    entry_by_index = {entry.index: entry for entry in entries}

    next_by_level: dict[int, int] = {}
    end_pages: dict[int, int] = {}
    for position in range(len(valid_sorted) - 1, -1, -1):
        entry = valid_sorted[position]
        possible_positions = [
            next_position
            for level, next_position in next_by_level.items()
            if level <= entry.level
        ]
        next_position = min(possible_positions) if possible_positions else None
        next_page = (
            valid_sorted[next_position].page_number
            if next_position is not None
            else None
        )
        end_pages[entry.index] = (
            max(entry.page_number or 1, (next_page or (page_count + 1)) - 1)
        )
        next_by_level[entry.level] = position

    insertion_entries = sorted(valid, key=lambda entry: entry.index)
    ordinal_by_index = {
        entry.index: ordinal
        for ordinal, entry in enumerate(valid_sorted, start=1)
    }
    for entry in insertion_entries:
        parent_index = entry.parent_index
        while parent_index is not None and parent_index not in section_id_by_outline_index:
            parent_entry = entry_by_index.get(parent_index)
            parent_index = parent_entry.parent_index if parent_entry else None
        parent_id = (
            section_id_by_outline_index[parent_index]
            if parent_index is not None
            else root_id
        )
        sections.append(
            ExtractionSection(
                id=section_id_by_outline_index[entry.index],
                parent_id=parent_id,
                ordinal=ordinal_by_index[entry.index],
                title=entry.title,
                hierarchy_path=" > ".join(entry.hierarchy),
                pdf_page_start=entry.page_number,
                pdf_page_end=end_pages[entry.index],
            )
        )
    return sections, section_id_by_outline_index


def extract_pdf(
    path: str | Path,
    *,
    version_id: str,
    document_title: str,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> PdfExtraction:
    source_path = Path(path)
    reader = PdfReader(source_path, strict=False)
    if reader.is_encrypted:
        result = reader.decrypt("")
        if result == 0:
            raise ValueError(f"PDF is encrypted and cannot be opened: {source_path}")

    page_count = len(reader.pages)
    outline_entries = extract_outline(reader)
    use_outline = len(outline_entries) >= MIN_USABLE_OUTLINE_ENTRIES
    selected_entries = outline_entries if use_outline else []
    sections, section_ids = build_outline_sections(
        entries=selected_entries,
        version_id=version_id,
        document_title=document_title,
        page_count=page_count,
    )
    root_id = sections[0].id

    sorted_entries = sorted(
        (
            entry
            for entry in selected_entries
            if entry.page_number is not None and 1 <= entry.page_number <= page_count
        ),
        key=lambda entry: (entry.page_number or 0, entry.index),
    )
    active_entry: OutlineEntry | None = None
    outline_position = 0
    chunks: list[ExtractionChunk] = []
    blank_page_count = 0
    chunk_ordinal = 0

    for page_number, page in enumerate(reader.pages, start=1):
        while (
            outline_position < len(sorted_entries)
            and (sorted_entries[outline_position].page_number or 0) <= page_number
        ):
            active_entry = sorted_entries[outline_position]
            outline_position += 1

        raw_text = page.extract_text() or ""
        text = normalize_extracted_text(raw_text)
        if not text:
            blank_page_count += 1
            continue

        fallback_heading = f"Page {page_number}"
        heading = (
            active_entry.title
            if active_entry is not None
            else infer_page_heading(text, fallback_heading)
        )
        hierarchy_path = (
            " > ".join(active_entry.hierarchy)
            if active_entry is not None
            else f"{document_title} > {heading}"
        )
        section_id = (
            section_ids.get(active_entry.index, root_id)
            if active_entry is not None
            else root_id
        )
        printed_page = detect_printed_page(text)

        for part, content in enumerate(
            split_page_text(text, max_characters=max_chunk_characters),
            start=1,
        ):
            content_hash = sha256_text(
                f"{version_id}\n{page_number}\n{part}\n{heading}\n{content}"
            )
            chunk_ordinal += 1
            chunks.append(
                ExtractionChunk(
                    id=_chunk_id(version_id, page_number, part, content_hash),
                    section_id=section_id,
                    ordinal=chunk_ordinal,
                    heading=heading,
                    hierarchy_path=hierarchy_path,
                    content=content,
                    pdf_pages=[page_number],
                    printed_pages=[printed_page] if printed_page else [],
                    content_hash=content_hash,
                    token_count=len(re.findall(r"\S+", content)),
                )
            )

    return PdfExtraction(
        page_count=page_count,
        outline_entry_count=len(outline_entries),
        used_outline=use_outline,
        blank_page_count=blank_page_count,
        sections=sections,
        chunks=chunks,
    )


def extract_registered_pdfs(
    store: KnowledgeStore,
    *,
    document_id: str | None = None,
    force: bool = False,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
    progress: Callable[[str], None] | None = None,
) -> list[PdfExtractionResult]:
    results: list[PdfExtractionResult] = []
    for record in store.registered_pdf_versions(document_id):
        source_path = Path(str(record["local_path"]))
        job_id, created = store.create_processing_job(
            job_type="extract-pdf",
            input_type="document_version",
            input_id=str(record["version_id"]),
            input_hash=str(record["checksum"]),
            processor_version=PDF_EXTRACTOR_VERSION,
        )
        existing_status = store.processing_job_status(job_id)
        if not created and not force and existing_status in {"completed", "running"}:
            results.append(
                PdfExtractionResult(
                    document_id=str(record["document_id"]),
                    version_id=str(record["version_id"]),
                    path=str(source_path),
                    status="skipped",
                )
            )
            continue
        if force:
            store.reset_processing_job(job_id)

        if progress:
            progress(f"Extracting {record['document_id']} from {source_path.name}")
        store.start_processing_job(job_id)
        try:
            extraction = extract_pdf(
                source_path,
                version_id=str(record["version_id"]),
                document_title=str(record["title"]),
                max_chunk_characters=max_chunk_characters,
            )
            store.replace_document_extraction(
                version_id=str(record["version_id"]),
                sections=extraction.sections,
                chunks=extraction.chunks,
            )
            store.complete_processing_job(job_id)
            result = PdfExtractionResult(
                document_id=str(record["document_id"]),
                version_id=str(record["version_id"]),
                path=str(source_path),
                status="completed",
                page_count=extraction.page_count,
                section_count=len(extraction.sections),
                chunk_count=len(extraction.chunks),
                blank_page_count=extraction.blank_page_count,
                used_outline=extraction.used_outline,
            )
            results.append(result)
            if progress:
                progress(
                    f"Completed {record['document_id']}: "
                    f"{result.page_count} pages, {result.chunk_count} chunks"
                )
        except Exception as error:
            store.fail_processing_job(job_id, str(error))
            results.append(
                PdfExtractionResult(
                    document_id=str(record["document_id"]),
                    version_id=str(record["version_id"]),
                    path=str(source_path),
                    status="failed",
                    error=str(error),
                )
            )
            if progress:
                progress(f"Failed {record['document_id']}: {error}")

    return results
