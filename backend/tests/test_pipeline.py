import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.genai.errors import APIError

from app import pipeline
from app import reviews
from app.rag.ingest import ingest
from app.rag.store import Chunk, LocalStore
import app.rag.retrieve as retrieve_module
from main import app


class PipelineTests(unittest.TestCase):
    def test_split_clauses_keeps_clause_references(self):
        text = (
            "1. Rent is due each month and must be paid by bank transfer.\n"
            "2.1 The tenant must notify the landlord before making any alterations."
        )

        clauses = pipeline.split_clauses(text)

        self.assertEqual([clause["clause_ref"] for clause in clauses], ["1", "2.1"])

    def test_analyze_downgrades_a_citation_not_in_retrieved_law(self):
        law = {"source": "Tenant Fees Act 2019", "section": "s.1", "text": "Fees", "url": "https://example.test/law"}
        analysis = {
            "summary": "A short summary.",
            "need_to_knows": [{"topic": "Rent", "clause_ref": "1", "text": "Rent is due monthly."}],
            "flags": [
                {
                    "kind": "contradicts_law",
                    "clause_ref": "1",
                    "clause_excerpt": "The tenant must pay a fee.",
                    "explanation": "A supported legal concern.",
                    "citation_source": "Tenant Fees Act 2019",
                    "citation_section": "s.1",
                },
                {
                    "kind": "contradicts_law",
                    "clause_ref": "2",
                    "clause_excerpt": "The tenant must pay a fee.",
                    "explanation": "This may be prohibited.",
                    "citation_source": "Tenant Fees Act 2019",
                    "citation_section": "s.99",
                }
            ],
        }
        with (
            patch.object(pipeline.config, "GEMINI_API_KEY", "test-key"),
            patch.object(pipeline, "extract_text", return_value=("1. Rent is due monthly. " * 3, 1)),
            patch("app.pipeline.retrieve_many", return_value=[[law]]),
            patch("app.pipeline.llm.analyze_agreement", return_value=analysis),
            patch("app.pipeline.llm.draft_landlord_email", return_value="Draft email") as draft,
        ):
            result = pipeline.analyze(b"%PDF-test", "Lead Tenant", "Example Street", ["Co Tenant"])

        self.assertEqual(result["flags"][0]["kind"], "contradicts_law")
        self.assertEqual(
            result["flags"][0]["citation"],
            {"source": "Tenant Fees Act 2019", "section": "s.1", "url": "https://example.test/law"},
        )
        self.assertEqual(result["flags"][1]["kind"], "clarify")
        self.assertIsNone(result["flags"][1]["citation"])
        self.assertIn("could not be verified", result["flags"][1]["explanation"])
        self.assertTrue(result["law_data_available"])
        draft.assert_called_once()
        self.assertEqual(draft.call_args.args[0], "Lead Tenant, Co Tenant")

    def test_analyze_rejects_a_pdf_without_extractable_text(self):
        with patch.object(pipeline, "extract_text", return_value=("", 2)):
            with self.assertRaisesRegex(pipeline.AnalysisInputError, "No selectable text"):
                pipeline.analyze(b"%PDF-test", "Tenant", "Example Street")

    def test_missing_api_key_returns_explicit_unanalyzed_response(self):
        with (
            patch.object(pipeline.config, "GEMINI_API_KEY", None),
            patch.object(pipeline, "extract_text", return_value=("Some selectable agreement text.", 1)),
            patch("app.pipeline.llm.analyze_agreement") as analyze_agreement,
        ):
            result = pipeline.analyze(b"%PDF-test", "Tenant", "Example Street")

        self.assertEqual(result["mode"], "placeholder")
        self.assertEqual(result["flags"], [])
        self.assertIn("not analyzed", result["summary"])
        analyze_agreement.assert_not_called()


class LawIngestionTests(unittest.TestCase):
    def test_retrieval_skips_embedding_when_law_store_is_empty(self):
        empty_store = unittest.mock.Mock()
        empty_store.is_empty.return_value = True
        with (
            patch("app.rag.retrieve._store", return_value=empty_store),
            patch("app.rag.retrieve.llm.embed") as embed,
        ):
            results = retrieve_module.retrieve_many(["clause one", "clause two"])

        self.assertEqual(results, [[], []])
        embed.assert_not_called()

    def test_reingestion_replaces_a_source_instead_of_duplicating_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalStore(Path(temp_dir) / "law_index.npz")
            with patch("app.rag.ingest.llm.embed", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]):
                ingest(store, [Chunk("Example Act", "s.1", "Old law text")])
                ingest(store, [Chunk("Example Act", "s.1", "Updated law text")])

            self.assertEqual(len(store.chunks), 1)
            self.assertEqual(store.chunks[0].text, "Updated law text")
            self.assertEqual(store.search([1.0, 0.0], 1)[0].text, "Updated law text")


class ReviewStorageTests(unittest.TestCase):
    def test_save_review_persists_review_and_tenant_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "reviews.sqlite3"
            review = {"summary": "A short review.", "flags": []}
            with patch.object(reviews.config, "REVIEWS_DB_PATH", database_path):
                review_id = reviews.save_review(
                    "Lead Tenant",
                    ["Co Tenant"],
                    "Example Street",
                    "agreement.pdf",
                    review,
                )

            with closing(sqlite3.connect(database_path)) as connection:
                row = connection.execute(
                    """
                    SELECT lead_tenant, co_tenants, property_address, filename, review
                    FROM tenancy_reviews WHERE id = ?
                    """,
                    (review_id,),
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Lead Tenant")
        self.assertEqual(json.loads(row[1]), ["Co Tenant"])
        self.assertEqual(row[2], "Example Street")
        self.assertEqual(row[3], "agreement.pdf")
        self.assertEqual(json.loads(row[4]), review)


class SavedReviewTests(unittest.TestCase):
    review = {"summary": "A short review.", "need_to_knows": [], "flags": []}

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for name, value in (
            ("DATABASE_URL", None),
            ("REVIEWS_DB_PATH", Path(temp_dir.name) / "reviews.sqlite3"),
        ):
            patcher = patch.object(reviews.config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_review_ids_are_readable_case_codes(self):
        code = reviews.save_review("Lead Tenant", [], "Example Street", None, self.review)
        self.assertRegex(code, r"^FLAT-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{6}$")
        self.assertNotEqual(code, reviews.save_review("Lead Tenant", [], "Example Street", None, self.review))

    def test_a_saved_review_reopens_by_case_code(self):
        code = reviews.save_review("Lead Tenant", ["Co Tenant"], "Example Street", "agreement.pdf", self.review)

        response = TestClient(app).get("/api/cases/" + code.removeprefix("FLAT-").lower())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["review_id"], code)
        self.assertEqual(body["summary"], "A short review.")
        self.assertEqual(body["tenants"], [
            {"name": "Lead Tenant", "role": "lead"},
            {"name": "Co Tenant", "role": "co_tenant"},
        ])

    def test_unknown_case_code_returns_404(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/cases/FLAT-ZZZZZZ").status_code, 404)
        reviews.save_review("Lead Tenant", [], "Example Street", None, self.review)
        self.assertEqual(client.get("/api/cases/FLAT-ZZZZZZ").status_code, 404)

    @unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
    def test_a_saved_review_reopens_from_postgres(self):
        with patch.object(reviews.config, "DATABASE_URL", os.getenv("TEST_DATABASE_URL")):
            code = reviews.save_review("Lead Tenant", ["Co Tenant"], "Example Street", None, self.review)
            saved = reviews.get_review(code)

        self.assertEqual(saved["co_tenants"], ["Co Tenant"])
        self.assertEqual(saved["summary"], "A short review.")


class AnalyzeApiTests(unittest.TestCase):
    def test_analyze_saves_review_to_configured_sqlite_database(self):
        client = TestClient(app)
        pipeline_result = {
            "pages": 1,
            "summary": "Persisted summary",
            "need_to_knows": [],
            "flags": [],
            "email": "",
            "mode": "gemini",
            "law_data_available": False,
            "disclaimer": "Information, not legal advice.",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "reviews.sqlite3"
            with (
                patch("main.pipeline.analyze", return_value=pipeline_result),
                patch.object(reviews.config, "DATABASE_URL", None),
                patch.object(reviews.config, "REVIEWS_DB_PATH", database_path),
            ):
                response = client.post(
                    "/api/analyze",
                    data={
                        "lead_tenant": "Lead Tenant",
                        "co_tenants": "Co Tenant",
                        "property_address": "Example Street",
                    },
                    files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
                )

            self.assertEqual(response.status_code, 200)
            review_id = response.json()["review_id"]
            with closing(sqlite3.connect(database_path)) as connection:
                row = connection.execute(
                    "SELECT review FROM tenancy_reviews WHERE id = ?", (review_id,)
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0])["summary"], "Persisted summary")

    def test_analyze_reports_busy_gemini_as_try_again_json(self):
        client = TestClient(app)
        with patch("main.pipeline.analyze", side_effect=APIError(
            code=503,
            response_json={"error": {"message": "high demand"}},
        )):
            response = client.post(
                "/api/analyze",
                data={
                    "lead_tenant": "Lead Tenant",
                    "property_address": "Example Street",
                },
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Gemini is busy", response.json()["detail"])

    def test_analyze_reports_gemini_api_failure_as_actionable_json(self):
        client = TestClient(app)
        with patch("main.pipeline.analyze", side_effect=APIError(
            code=400,
            response_json={"error": {"message": "Bad request"}},
        )):
            response = client.post(
                "/api/analyze",
                data={
                    "lead_tenant": "Lead Tenant",
                    "property_address": "Example Street",
                },
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Gemini could not analyze", response.json()["detail"])

    def test_analyze_accepts_the_planned_form_fields(self):
        client = TestClient(app)
        pipeline_result = {
            "pages": 1,
            "summary": "Summary",
            "need_to_knows": [],
            "flags": [],
            "email": "",
            "mode": "gemini",
            "law_data_available": True,
            "disclaimer": "Information, not legal advice.",
        }
        with (
            patch("main.pipeline.analyze", return_value=pipeline_result) as analyze,
            patch("main.reviews.save_review", return_value="review-id") as save_review,
        ):
            response = client.post(
                "/api/analyze",
                data={
                    "lead_tenant": "Lead Tenant",
                    "co_tenants": "First Co-tenant, Second Co-tenant",
                    "property_address": "Example Street",
                },
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tenants"], [
            {"name": "Lead Tenant", "role": "lead"},
            {"name": "First Co-tenant", "role": "co_tenant"},
            {"name": "Second Co-tenant", "role": "co_tenant"},
        ])
        self.assertEqual(body["summary"], "Summary")
        self.assertEqual(body["review_id"], "review-id")
        analyze.assert_called_once_with(
            b"%PDF-test",
            "Lead Tenant",
            "Example Street",
            ["First Co-tenant", "Second Co-tenant"],
        )
        save_review.assert_called_once_with(
            "Lead Tenant",
            ["First Co-tenant", "Second Co-tenant"],
            "Example Street",
            "agreement.pdf",
            pipeline_result,
        )

    def test_analyze_returns_error_if_review_cannot_be_saved(self):
        client = TestClient(app)
        with (
            patch("main.pipeline.analyze", return_value={"summary": "Summary"}),
            patch("main.reviews.save_review", side_effect=OSError("Database is unavailable")),
        ):
            response = client.post(
                "/api/analyze",
                data={"lead_tenant": "Lead Tenant", "property_address": "Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("could not be saved", response.json()["detail"])

    def test_analyze_keeps_legacy_tenant_name_field_compatible(self):
        client = TestClient(app)
        result = {
            "pages": 1,
            "summary": "Summary",
            "need_to_knows": [],
            "flags": [],
            "email": "",
            "mode": "placeholder",
            "law_data_available": False,
            "disclaimer": "Information, not legal advice.",
        }
        with (
            patch("main.pipeline.analyze", return_value=result),
            patch("main.reviews.save_review", return_value="review-id"),
        ):
            response = client.post(
                "/api/analyze",
                data={"tenant_name": "Legacy Tenant", "property_address": "Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lead_tenant"], "Legacy Tenant")


if __name__ == "__main__":
    unittest.main()
