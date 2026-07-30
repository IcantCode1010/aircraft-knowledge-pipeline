from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from aircraft_knowledge_pipeline.store import KnowledgeStore, sha256_text
from aircraft_knowledge_pipeline.cli import main as cli_main
from aircraft_knowledge_pipeline.intake import register_source_tree
from aircraft_knowledge_pipeline.pdf_extractor import (
    OutlineEntry,
    build_outline_sections,
    detect_printed_page,
    extract_pdf,
    extract_registered_pdfs,
    split_page_text,
)
from aircraft_knowledge_pipeline.topic_discovery import (
    candidate_from_section,
    discover_topic_candidates,
)
from aircraft_knowledge_pipeline.topic_canonicalization import (
    ata_from_hierarchy,
    promote_topic_candidates,
)
from aircraft_knowledge_pipeline.topic_enrichment import (
    DEFAULT_ENRICHMENT_TYPES,
    enrich_topics,
)
from aircraft_knowledge_pipeline.evidence_review import (
    classify_evidence,
    triage_evidence,
)
from aircraft_knowledge_pipeline.editorial_draft import build_editorial_draft
from aircraft_knowledge_pipeline.okf_preview import build_okf_preview
from aircraft_knowledge_pipeline.research_packet import build_research_packet
from aircraft_knowledge_pipeline.source_policy import (
    CORE_SOURCE_TYPES,
    DEFAULT_EXCLUDED_SOURCE_TYPES,
    OPTIONAL_SOURCE_TYPES,
)


def write_text_pdf(path: Path, pages: list[str], outline_items: int = 0) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)

    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)

    for index in range(outline_items):
        writer.add_outline_item(
            f"Section {index + 1} Cargo Door",
            min(index, len(pages) - 1),
        )

    with path.open("wb") as destination:
        writer.write(destination)


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pipeline.db"
        self.store = KnowledgeStore(self.database_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def register_source(
        self,
        *,
        document_id: str,
        document_type: str,
        title: str,
        aircraft: str = "737 NG",
        operator: str = "",
    ) -> str:
        self.store.register_document(
            document_id=document_id,
            file_name=f"{document_id}.pdf",
            title=title,
            document_type=document_type,
            source_authority="Test Authority",
        )
        return self.store.register_document_version(
            document_id=document_id,
            checksum=sha256_text(document_id),
            revision="1",
            status="current",
            aircraft=[aircraft],
            operator=operator,
        )

    def test_initializes_schema_and_fts5(self) -> None:
        self.assertEqual(self.store.schema_version(), "4")
        tables = {
            row["name"]
            for row in self.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        self.assertIn("documents", tables)
        self.assertIn("source_chunks", tables)
        self.assertIn("source_chunks_fts", tables)
        self.assertIn("processing_jobs", tables)
        self.assertIn("evidence_reviews", tables)

    def test_indexes_and_filters_page_aware_chunks(self) -> None:
        mel_version = self.register_source(
            document_id="operator-mel",
            document_type="mel",
            title="Operator MEL",
            operator="Example Operator",
        )
        amm_version = self.register_source(
            document_id="chapter-52",
            document_type="amm",
            title="AMM Chapter 52",
        )

        mel_section = self.store.add_section(
            version_id=mel_version,
            title="Cargo Door",
            hierarchy_path="MEL > ATA 52 > Cargo Door",
            ordinal=1,
            pdf_page_start=412,
            pdf_page_end=413,
        )
        self.store.add_chunk(
            version_id=mel_version,
            section_id=mel_section,
            ordinal=1,
            heading="Cargo Door",
            hierarchy_path="MEL > ATA 52 > Cargo Door",
            content="Dispatch relief for an inoperative cargo compartment door.",
            pdf_pages=[412, 413],
            printed_pages=["52-31-01 Page 1", "52-31-01 Page 2"],
        )
        self.store.add_chunk(
            version_id=amm_version,
            ordinal=1,
            heading="Cargo Door Warning System",
            hierarchy_path="ATA 52 > Cargo Doors > Warning System",
            content="The warning system monitors cargo door position.",
            pdf_pages=[184],
            printed_pages=["52-71-00 Page 4"],
        )

        all_results = self.store.search_chunks("cargo door")
        mel_results = self.store.search_chunks(
            "cargo door",
            document_type="mel",
            aircraft="737 NG",
            operator="Example Operator",
        )

        self.assertEqual(len(all_results), 2)
        self.assertEqual(len(mel_results), 1)
        self.assertEqual(mel_results[0].document_type, "mel")
        self.assertEqual(mel_results[0].pdf_pages, [412, 413])
        self.assertEqual(
            mel_results[0].printed_pages,
            ["52-31-01 Page 1", "52-31-01 Page 2"],
        )

    def test_deduplicates_chunks_and_processing_jobs(self) -> None:
        version_id = self.register_source(
            document_id="chapter-28",
            document_type="amm",
            title="AMM Chapter 28",
        )
        first_chunk = self.store.add_chunk(
            version_id=version_id,
            ordinal=1,
            heading="Fuel Quantity",
            hierarchy_path="ATA 28 > Fuel Quantity",
            content="Fuel quantity source content.",
            pdf_pages=[20],
        )
        second_chunk = self.store.add_chunk(
            version_id=version_id,
            ordinal=1,
            heading="Fuel Quantity",
            hierarchy_path="ATA 28 > Fuel Quantity",
            content="Fuel quantity source content.",
            pdf_pages=[20],
        )
        self.assertEqual(first_chunk, second_chunk)

        first_job, first_created = self.store.create_processing_job(
            job_type="extract",
            input_type="document_version",
            input_id=version_id,
            input_hash="input-hash",
            processor_version="extractor-1",
        )
        second_job, second_created = self.store.create_processing_job(
            job_type="extract",
            input_type="document_version",
            input_id=version_id,
            input_hash="input-hash",
            processor_version="extractor-1",
        )
        self.assertEqual(first_job, second_job)
        self.assertTrue(first_created)
        self.assertFalse(second_created)

    def test_caches_negative_topic_searches(self) -> None:
        version_id = self.register_source(
            document_id="operator-mel",
            document_type="mel",
            title="Operator MEL",
        )
        self.store.create_topic(
            topic_id="b737ng-fuel-temperature",
            slug="fuel-temperature",
            title="Fuel Temperature",
            aircraft="737 NG",
            ata=["28"],
        )
        self.store.add_topic_aliases(
            "b737ng-fuel-temperature",
            ["Fuel Temperature", "Fuel Temp"],
        )

        fingerprint = sha256_text(
            json.dumps(
                {
                    "aliases": ["fuel temperature", "fuel temp"],
                    "document_version": version_id,
                },
                sort_keys=True,
            )
        )
        first = self.store.record_topic_search(
            topic_id="b737ng-fuel-temperature",
            version_id=version_id,
            search_type="mel-enrichment",
            query_fingerprint=fingerprint,
            status="not_found",
            processor_version="search-1",
            searched_aliases=["Fuel Temperature", "Fuel Temp"],
        )
        second = self.store.record_topic_search(
            topic_id="b737ng-fuel-temperature",
            version_id=version_id,
            search_type="mel-enrichment",
            query_fingerprint=fingerprint,
            status="not_found",
            processor_version="search-1",
            searched_aliases=["Fuel Temperature", "Fuel Temp"],
        )

        self.assertEqual(first, second)
        count = self.store.connection.execute(
            "SELECT COUNT(*) FROM topic_searches"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_foreign_keys_are_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.register_document_version(
                document_id="missing-document",
                checksum="missing",
            )


class CliTests(unittest.TestCase):
    def test_initializes_and_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "cli.db"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    cli_main(["--db", str(database_path), "init-db"]),
                    0,
                )
                self.assertEqual(
                    cli_main(["--db", str(database_path), "status"]),
                    0,
                )
            rendered = output.getvalue()
            self.assertIn("schema version 4", rendered)
            self.assertIn('"documents": 0', rendered)

    def test_refuses_missing_database_for_non_init_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "missing.db"
            with self.assertRaisesRegex(SystemExit, "Run init-db first"):
                cli_main(["--db", str(database_path), "status"])
            self.assertFalse(database_path.exists())

    def test_registers_complete_source_tree_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "sources" / "documents"
            (source_root / "qrh").mkdir(parents=True)
            (source_root / "visual-reference").mkdir(parents=True)
            (source_root / "qrh" / "737 QRH.pdf").write_bytes(b"qrh prototype")
            (source_root / "visual-reference" / "panel_NG.png").write_bytes(
                b"png prototype"
            )
            (source_root / "qrh" / ".gitkeep").write_text("", encoding="utf-8")
            (source_root / "qrh" / "unsupported.bin").write_bytes(b"skip")

            database_path = root / "pipeline.db"
            with KnowledgeStore(database_path) as store:
                store.initialize()
                first = register_source_tree(store, source_root)
                second = register_source_tree(store, source_root)
                counts = store.status_counts()
                records = store.connection.execute(
                    """
                    SELECT id, document_type, file_name
                    FROM documents
                    ORDER BY id
                    """
                ).fetchall()

            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 2)
            self.assertEqual(counts["documents"], 2)
            self.assertEqual(counts["document_versions"], 2)
            self.assertEqual(
                [tuple(record) for record in records],
                [
                    ("qrh-737-qrh", "qrh", "737 QRH.pdf"),
                    (
                        "visual-reference-panel-ng",
                        "visual-reference",
                        "panel_NG.png",
                    ),
                ],
            )


class PdfExtractorTests(unittest.TestCase):
    def test_detects_mmel_and_split_line_amm_page_labels(self) -> None:
        self.assertEqual(
            detect_printed_page("MASTER MINIMUM EQUIPMENT LIST\nPAGE NO. 52-19"),
            "PAGE NO. 52-19",
        )
        self.assertEqual(
            detect_printed_page("AIRCRAFT MAINTENANCE MANUAL\n21-00-01\nPage 208"),
            "21-00-01 Page 208",
        )

    def test_outline_sections_are_parent_first_when_pages_are_out_of_order(self) -> None:
        entries = [
            OutlineEntry(
                index=0,
                parent_index=None,
                level=0,
                page_number=5,
                title="Parent",
                hierarchy=("Parent",),
            ),
            OutlineEntry(
                index=1,
                parent_index=0,
                level=1,
                page_number=2,
                title="Child",
                hierarchy=("Parent", "Child"),
            ),
        ]
        sections, section_ids = build_outline_sections(
            entries=entries,
            version_id="out-of-order",
            document_title="Test Document",
            page_count=10,
        )
        self.assertEqual(sections[1].id, section_ids[0])
        self.assertEqual(sections[2].parent_id, section_ids[0])

    def test_splits_large_page_without_crossing_limit(self) -> None:
        text = "\n".join(f"Line {index} " + ("x" * 70) for index in range(30))
        pieces = split_page_text(text, max_characters=500)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= 500 for piece in pieces))

    def test_uses_sparse_outline_fallback_and_inferred_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf_path = Path(temporary_directory) / "sparse.pdf"
            write_text_pdf(
                pdf_path,
                ["CARGO DOOR PROCEDURE Page 1"],
                outline_items=1,
            )
            extraction = extract_pdf(
                pdf_path,
                version_id="sparse-version",
                document_title="Sparse MEL",
            )

            self.assertFalse(extraction.used_outline)
            self.assertEqual(len(extraction.sections), 1)
            self.assertEqual(len(extraction.chunks), 1)
            self.assertEqual(extraction.chunks[0].heading, "CARGO DOOR PROCEDURE Page 1")
            self.assertEqual(extraction.chunks[0].pdf_pages, [1])

    def test_extracts_registered_pdf_and_skips_completed_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pdf_path = root / "chapter-52.pdf"
            write_text_pdf(
                pdf_path,
                [
                    "CARGO DOOR SYSTEM DESCRIPTION Page 1",
                    "CARGO DOOR WARNING SYSTEM Page 2",
                ],
                outline_items=10,
            )
            database_path = root / "pipeline.db"

            with KnowledgeStore(database_path) as store:
                store.initialize()
                store.register_document(
                    document_id="chapter-52",
                    file_name=pdf_path.name,
                    title="Chapter 52",
                    document_type="amm",
                )
                version_id = store.register_document_version(
                    document_id="chapter-52",
                    checksum="test-checksum",
                    local_path=str(pdf_path),
                    status="current",
                )

                first = extract_registered_pdfs(store)
                second = extract_registered_pdfs(store)
                forced = extract_registered_pdfs(store, force=True)
                results = store.search_chunks("cargo door", document_type="amm")
                status = store.status_counts()

            self.assertEqual(first[0].status, "completed")
            self.assertEqual(first[0].page_count, 2)
            self.assertTrue(first[0].used_outline)
            self.assertEqual(second[0].status, "skipped")
            self.assertEqual(forced[0].status, "completed")
            self.assertEqual(status["chunks"], 2)
            self.assertEqual(status["sections"], 11)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].document_version_id, version_id)


class TopicDiscoveryTests(unittest.TestCase):
    def test_parses_subject_heading_and_ignores_non_subject_sections(self) -> None:
        candidate = candidate_from_section(
            version_id="training-version",
            section_id="section-1",
            title="Subject 21-25-00 - Recirculation System",
            hierarchy_path="Chapter 21 > Subject 21-25-00 - Recirculation System",
        )
        ignored = candidate_from_section(
            version_id="training-version",
            section_id="section-2",
            title="Pageset 21-25-00-001 - Introduction",
            hierarchy_path="Chapter 21 > Pageset",
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.title, "Recirculation System")
        self.assertEqual(candidate.confidence, 0.98)
        self.assertIsNone(ignored)

    def test_discovers_training_topics_and_skips_completed_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "pipeline.db"
            with KnowledgeStore(database_path) as store:
                store.initialize()
                store.register_document(
                    document_id="chapter-21-training",
                    file_name="chapter-21.pdf",
                    title="Chapter 21 Training",
                    document_type="training",
                )
                version_id = store.register_document_version(
                    document_id="chapter-21-training",
                    checksum="training-checksum",
                    status="current",
                )
                store.add_section(
                    version_id=version_id,
                    title="Subject 21-25-00 - Recirculation System",
                    hierarchy_path=(
                        "Chapter 21 - Air Conditioning > "
                        "Subject 21-25-00 - Recirculation System"
                    ),
                    ordinal=1,
                    pdf_page_start=63,
                    pdf_page_end=70,
                )
                store.add_section(
                    version_id=version_id,
                    title="Pageset 21-25-00-001 - Introduction",
                    hierarchy_path="Chapter 21 > Pageset 21-25-00-001",
                    ordinal=2,
                    pdf_page_start=64,
                    pdf_page_end=65,
                )

                first = discover_topic_candidates(store)
                second = discover_topic_candidates(store)
                forced = discover_topic_candidates(store, force=True)
                rows = store.connection.execute(
                    """
                    SELECT title, confidence, status
                    FROM topic_candidates
                    ORDER BY title
                    """
                ).fetchall()

            self.assertEqual(first[0].status, "completed")
            self.assertEqual(first[0].candidate_count, 1)
            self.assertEqual(second[0].status, "skipped")
            self.assertEqual(forced[0].status, "completed")
            self.assertEqual(
                [tuple(row) for row in rows],
                [("Recirculation System", 0.98, "candidate")],
            )


class TopicWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pipeline.db"
        self.store = KnowledgeStore(self.database_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def register_version(self, document_id: str, document_type: str) -> str:
        self.store.register_document(
            document_id=document_id,
            file_name=f"{document_id}.pdf",
            title=document_id,
            document_type=document_type,
        )
        return self.store.register_document_version(
            document_id=document_id,
            checksum=f"{document_id}-checksum",
            status="current",
        )

    def test_condensed_source_policy_defaults(self) -> None:
        self.assertEqual(CORE_SOURCE_TYPES, ("training", "amm"))
        self.assertEqual(DEFAULT_ENRICHMENT_TYPES, CORE_SOURCE_TYPES)
        self.assertEqual(OPTIONAL_SOURCE_TYPES, ("mel", "qrh"))
        self.assertEqual(DEFAULT_EXCLUDED_SOURCE_TYPES, ("fcom",))

    def test_promotes_candidate_with_stable_topic_alias_and_ata(self) -> None:
        version_id = self.register_version("training-21", "training")
        self.store.add_section(
            version_id=version_id,
            title="Subject 21-25-00 - Recirculation System",
            hierarchy_path=(
                "Chapter 21 - Air Conditioning > "
                "Subject 21-25-00 - Recirculation System"
            ),
            ordinal=1,
        )
        discover_topic_candidates(self.store)

        promoted = promote_topic_candidates(
            self.store,
            aircraft="737 NG",
            accept_all=True,
        )
        topic = self.store.connection.execute(
            "SELECT id, slug, title, aircraft, ata_json FROM topics"
        ).fetchone()
        candidate = self.store.connection.execute(
            "SELECT status, proposed_topic_id FROM topic_candidates"
        ).fetchone()

        self.assertEqual(len(promoted), 1)
        self.assertEqual(
            tuple(topic),
            (
                "737ng-recirculation-system",
                "recirculation-system",
                "Recirculation System",
                "737 NG",
                '["21"]',
            ),
        )
        self.assertEqual(
            tuple(candidate),
            ("accepted", "737ng-recirculation-system"),
        )
        self.assertEqual(
            self.store.aliases_for_topic("737ng-recirculation-system"),
            ["Recirculation System"],
        )
        self.assertEqual(
            ata_from_hierarchy("Chapter 21 > Subject 21-25-00"),
            ("21",),
        )

    def test_enrichment_records_found_not_found_evidence_and_skips(self) -> None:
        mel_version = self.register_version("operator-mel", "mel")
        qrh_version = self.register_version("operator-qrh", "qrh")
        mel_section = self.store.add_section(
            version_id=mel_version,
            title="Recirculation System",
            hierarchy_path="MEL > ATA 21 > Recirculation System",
            ordinal=1,
        )
        self.store.add_chunk(
            version_id=mel_version,
            section_id=mel_section,
            ordinal=1,
            heading="Recirculation System",
            hierarchy_path="MEL > ATA 21 > Recirculation System",
            content="The recirculation system may be inoperative for dispatch.",
            pdf_pages=[12],
        )
        self.store.add_section(
            version_id=qrh_version,
            title="Air Systems",
            hierarchy_path="QRH > Air Systems",
            ordinal=1,
        )
        self.store.add_chunk(
            version_id=qrh_version,
            ordinal=1,
            heading="Air Systems",
            hierarchy_path="QRH > Air Systems",
            content="Pack trip procedure.",
            pdf_pages=[20],
        )
        self.store.create_topic(
            topic_id="737ng-recirculation-system",
            slug="recirculation-system",
            title="Recirculation System",
            aircraft="737 NG",
            ata=["21"],
        )
        self.store.add_topic_aliases(
            "737ng-recirculation-system",
            ["Recirculation System"],
        )

        first = enrich_topics(
            self.store,
            document_types=("mel", "qrh"),
        )
        second = enrich_topics(
            self.store,
            document_types=("mel", "qrh"),
        )
        searches = self.store.connection.execute(
            "SELECT status FROM topic_searches ORDER BY status"
        ).fetchall()
        evidence_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM topic_evidence"
        ).fetchone()[0]

        self.assertEqual(
            sorted(result.status for result in first),
            ["found", "not_found"],
        )
        self.assertTrue(all(result.status == "skipped" for result in second))
        self.assertEqual([row["status"] for row in searches], ["found", "not_found"])
        self.assertEqual(evidence_count, 1)

    def test_enrichment_refresh_preserves_reviewed_evidence(self) -> None:
        training_version = self.register_version("chapter-training", "training")
        section_id = self.store.add_section(
            version_id=training_version,
            title="Recirculation System",
            hierarchy_path="Training > Recirculation System",
            ordinal=1,
        )
        chunk_id = self.store.add_chunk(
            version_id=training_version,
            section_id=section_id,
            ordinal=1,
            heading="Recirculation System",
            hierarchy_path="Training > Recirculation System",
            content="The recirculation system supplies ventilation air.",
            pdf_pages=[12],
        )
        self.store.create_topic(
            topic_id="737ng-recirculation-system",
            slug="recirculation-system",
            title="Recirculation System",
            aircraft="737 NG",
            ata=["21"],
        )
        self.store.add_topic_aliases(
            "737ng-recirculation-system",
            ["Recirculation System"],
        )

        enrich_topics(
            self.store,
            topic_ids=("737ng-recirculation-system",),
            document_types=("training",),
        )
        triage_evidence(
            self.store,
            topic_ids=("737ng-recirculation-system",),
        )
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=chunk_id,
            evidence_role="training_support",
            review_status="approved",
            reviewer="test-reviewer",
        )
        enrich_topics(
            self.store,
            topic_ids=("737ng-recirculation-system",),
            document_types=("training",),
            max_evidence_per_document=2,
            force=True,
        )
        review = self.store.connection.execute(
            """
            SELECT er.review_status, er.reviewer, te.review_status
            FROM evidence_reviews er
            JOIN topic_evidence te
              USING(topic_id, chunk_id, evidence_role)
            """
        ).fetchone()

        self.assertEqual(
            tuple(review),
            ("approved", "test-reviewer", "approved"),
        )

    def test_triages_and_preserves_manual_evidence_decisions(self) -> None:
        mel_version = self.register_version("operator-mel", "mel")
        section_id = self.store.add_section(
            version_id=mel_version,
            title="Recirculation System",
            hierarchy_path="MEL > ATA 21 > Recirculation System",
            ordinal=1,
        )
        chunk_id = self.store.add_chunk(
            version_id=mel_version,
            section_id=section_id,
            ordinal=1,
            heading="Recirculation System",
            hierarchy_path="MEL > ATA 21 > Recirculation System",
            content=(
                "NO. REQUIRED FOR DISPATCH. Recirculation System may be "
                "inoperative provided the remaining fan operates normally."
            ),
            pdf_pages=[12],
            printed_pages=["PAGE NO. 21-12"],
        )
        self.store.create_topic(
            topic_id="737ng-recirculation-system",
            slug="recirculation-system",
            title="Recirculation System",
            aircraft="737 NG",
            ata=["21"],
        )
        search_id = self.store.record_topic_search(
            topic_id="737ng-recirculation-system",
            version_id=mel_version,
            search_type="mel-enrichment",
            query_fingerprint="fingerprint",
            status="found",
            processor_version="test-search",
        )
        self.store.add_topic_evidence(
            topic_id="737ng-recirculation-system",
            chunk_id=chunk_id,
            search_id=search_id,
            evidence_role="mel_support",
        )

        triaged = triage_evidence(self.store)
        queue = self.store.evidence_review_queue()
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=chunk_id,
            evidence_role="mel_support",
            review_status="approved",
            reviewer="test-reviewer",
        )
        rerun = triage_evidence(self.store)
        review = self.store.connection.execute(
            """
            SELECT classification, review_status, reviewer
            FROM evidence_reviews
            """
        ).fetchone()
        evidence = self.store.connection.execute(
            "SELECT review_status FROM topic_evidence"
        ).fetchone()

        self.assertEqual(triaged[0].classification, "procedure_candidate")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["printed_pages_json"], '["PAGE NO. 21-12"]')
        self.assertFalse(rerun[0].stored)
        self.assertEqual(
            tuple(review),
            ("procedure_candidate", "approved", "test-reviewer"),
        )
        self.assertEqual(evidence["review_status"], "approved")

    def test_classifies_blank_and_fcom_reference_pages(self) -> None:
        blank = classify_evidence(
            topic_title="Cooling",
            document_type="qrh",
            heading="Brake Cooling Schedule",
            hierarchy_path="QRH > Performance",
            content="Intentionally Blank",
        )
        reference = classify_evidence(
            topic_title="Equipment Cooling System",
            document_type="fcom",
            heading="Equipment Cooling System",
            hierarchy_path="FCOM > Air Systems > Equipment Cooling System",
            content="The equipment cooling system supplies air to displays.",
        )

        self.assertEqual(blank[0], "incidental")
        self.assertEqual(reference[0], "supporting_reference")

    def test_builds_packet_from_approved_evidence_only(self) -> None:
        training_version = self.register_version("chapter-training", "training")
        approved_chunk = self.store.add_chunk(
            version_id=training_version,
            ordinal=1,
            heading="Recirculation Fans",
            hierarchy_path="Training > Air Systems > Recirculation Fans",
            content="Approved recirculation system description.",
            pdf_pages=[31],
            printed_pages=["2.31.6"],
        )
        rejected_chunk = self.store.add_chunk(
            version_id=training_version,
            ordinal=2,
            heading="Table Of Contents",
            hierarchy_path="Training > Table Of Contents",
            content="Rejected incidental recirculation system listing.",
            pdf_pages=[2],
        )
        fcom_version = self.register_version("operator-fcom", "fcom")
        excluded_fcom_chunk = self.store.add_chunk(
            version_id=fcom_version,
            ordinal=1,
            heading="Recirculation Fans",
            hierarchy_path="FCOM > Air Systems > Recirculation Fans",
            content="Approved FCOM evidence excluded from the core profile.",
            pdf_pages=[40],
        )
        self.store.create_topic(
            topic_id="737ng-recirculation-system",
            slug="recirculation-system",
            title="Recirculation System",
            aircraft="737 NG",
            ata=["21"],
        )
        training_search_id = self.store.record_topic_search(
            topic_id="737ng-recirculation-system",
            version_id=training_version,
            search_type="training-enrichment",
            query_fingerprint="training-packet-fingerprint",
            status="found",
            processor_version="test-search",
        )
        for chunk_id in (approved_chunk, rejected_chunk):
            self.store.add_topic_evidence(
                topic_id="737ng-recirculation-system",
                chunk_id=chunk_id,
                search_id=training_search_id,
                evidence_role="training_support",
            )
        fcom_search_id = self.store.record_topic_search(
            topic_id="737ng-recirculation-system",
            version_id=fcom_version,
            search_type="fcom-enrichment",
            query_fingerprint="fcom-packet-fingerprint",
            status="found",
            processor_version="test-search",
        )
        self.store.add_topic_evidence(
            topic_id="737ng-recirculation-system",
            chunk_id=excluded_fcom_chunk,
            search_id=fcom_search_id,
            evidence_role="fcom_support",
        )
        triage_evidence(self.store)
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=approved_chunk,
            evidence_role="training_support",
            review_status="approved",
            reviewer="test-reviewer",
        )
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=rejected_chunk,
            evidence_role="training_support",
            review_status="rejected",
            reviewer="test-reviewer",
        )
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=excluded_fcom_chunk,
            evidence_role="fcom_support",
            review_status="approved",
            reviewer="test-reviewer",
        )

        output_root = Path(self.temporary_directory.name) / "packets"
        packet = build_research_packet(
            self.store,
            topic_id="737ng-recirculation-system",
            output_root=output_root,
        )
        content = Path(packet.path).read_text(encoding="utf-8")
        expanded_packet = build_research_packet(
            self.store,
            topic_id="737ng-recirculation-system",
            output_root=Path(self.temporary_directory.name) / "expanded-packets",
            source_profile="all-approved",
        )
        expanded_content = Path(expanded_packet.path).read_text(encoding="utf-8")
        artifact_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_type='research_packet'"
        ).fetchone()[0]

        self.assertEqual(packet.approved_evidence_count, 1)
        self.assertEqual(packet.excluded_approved_evidence_count, 1)
        self.assertEqual(packet.source_profile, "core")
        self.assertIn("Approved recirculation system description", content)
        self.assertNotIn("Approved FCOM evidence", content)
        self.assertNotIn("Rejected incidental", content)
        self.assertIn("printed 2.31.6", content)
        self.assertEqual(expanded_packet.approved_evidence_count, 2)
        self.assertIn("Approved FCOM evidence", expanded_content)
        self.assertEqual(artifact_count, 2)

    def test_refuses_packet_without_approved_evidence(self) -> None:
        self.store.create_topic(
            topic_id="737ng-empty-topic",
            slug="empty-topic",
            title="Empty Topic",
            aircraft="737 NG",
            ata=["21"],
        )
        with self.assertRaisesRegex(ValueError, "no approved evidence"):
            build_research_packet(
                self.store,
                topic_id="737ng-empty-topic",
                output_root=Path(self.temporary_directory.name) / "packets",
            )

    def test_builds_reviewable_okf_content_form_from_structured_claims(self) -> None:
        training_version = self.register_version("chapter-training", "training")
        chunk_id = self.store.add_chunk(
            version_id=training_version,
            ordinal=1,
            heading="Recirculation System Introduction",
            hierarchy_path="Training > Recirculation System > Introduction",
            content="The system combines recirculated cabin air with pack air.",
            pdf_pages=[12],
            printed_pages=["Page 2"],
        )
        self.store.create_topic(
            topic_id="737ng-recirculation-system",
            slug="recirculation-system",
            title="Recirculation System",
            aircraft="737 NG",
            ata=["21"],
        )
        search_id = self.store.record_topic_search(
            topic_id="737ng-recirculation-system",
            version_id=training_version,
            search_type="training-enrichment",
            query_fingerprint="preview-fingerprint",
            status="found",
            processor_version="test-search",
        )
        self.store.add_topic_evidence(
            topic_id="737ng-recirculation-system",
            chunk_id=chunk_id,
            search_id=search_id,
            evidence_role="training_support",
        )
        triage_evidence(
            self.store,
            topic_ids=("737ng-recirculation-system",),
        )
        self.store.decide_evidence_review(
            topic_id="737ng-recirculation-system",
            chunk_id=chunk_id,
            evidence_role="training_support",
            review_status="approved",
            reviewer="test-reviewer",
        )
        claim_id = self.store.upsert_content_claim(
            topic_id="737ng-recirculation-system",
            section_key="overview",
            claim_text=(
                "The system combines recirculated cabin air with conditioned "
                "air from the packs."
            ),
            chunk_ids=(chunk_id,),
            sort_order=10,
            applicability="Configuration shown in the approved Training source.",
        )

        result = build_okf_preview(
            self.store,
            topic_id="737ng-recirculation-system",
            output_root=Path(self.temporary_directory.name) / "previews",
        )
        content = Path(result.path).read_text(encoding="utf-8")

        self.assertEqual(result.claim_count, 1)
        self.assertEqual(result.approved_claim_count, 0)
        self.assertIn("## Overview", content)
        self.assertIn("## Evidence map", content)
        self.assertIn("Claim status: needs review", content)
        self.assertIn(str(chunk_id), content)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT review_status FROM claims WHERE id = ?",
                (claim_id,),
            ).fetchone()[0],
            "needs_review",
        )

        with self.assertRaisesRegex(ValueError, "must be approved"):
            build_editorial_draft(
                self.store,
                topic_id="737ng-recirculation-system",
                output_root=Path(self.temporary_directory.name) / "editorial",
            )

        approved_model = self.store.decide_latest_artifact(
            topic_id="737ng-recirculation-system",
            artifact_type="okf_preview",
            review_status="approved",
            reviewer="content-owner",
        )
        rebuilt_preview = build_okf_preview(
            self.store,
            topic_id="737ng-recirculation-system",
            output_root=Path(self.temporary_directory.name) / "previews",
        )
        editorial = build_editorial_draft(
            self.store,
            topic_id="737ng-recirculation-system",
            output_root=Path(self.temporary_directory.name) / "editorial",
        )
        editorial_content = Path(editorial.path).read_text(encoding="utf-8")
        retained_status = self.store.connection.execute(
            "SELECT review_status FROM artifacts WHERE id = ?",
            (approved_model["id"],),
        ).fetchone()[0]

        self.assertEqual(rebuilt_preview.content_hash, result.content_hash)
        self.assertEqual(retained_status, "approved")
        self.assertEqual(editorial.content_model_hash, result.content_hash)
        self.assertIn("editorial_status: needs_review", editorial_content)
        self.assertIn("Training overview only", editorial_content)
        self.assertIn("## Sources", editorial_content)
        self.assertNotIn("Claim status:", editorial_content)


if __name__ == "__main__":
    unittest.main()
