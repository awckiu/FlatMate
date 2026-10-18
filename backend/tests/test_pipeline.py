import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.genai import errors as gemini_errors

from app import pipeline
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


class AnalyzeApiTests(unittest.TestCase):
    def setUp(self):
        # Never save test uploads to the real database from a developer's .env.
        patcher = patch.object(pipeline.config, "DATABASE_URL", None)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        with patch("main.pipeline.analyze", return_value=pipeline_result) as analyze:
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
        analyze.assert_called_once_with(
            b"%PDF-test",
            "Lead Tenant",
            "Example Street",
            ["First Co-tenant", "Second Co-tenant"],
        )

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
        with patch("main.pipeline.analyze", return_value=result):
            response = client.post(
                "/api/analyze",
                data={"tenant_name": "Legacy Tenant", "property_address": "Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lead_tenant"], "Legacy Tenant")

    def test_analyze_returns_a_json_error_when_gemini_is_busy(self):
        client = TestClient(app)
        busy = gemini_errors.ServerError(503, {"error": {"message": "high demand"}})
        with patch("main.pipeline.analyze", side_effect=busy):
            response = client.post(
                "/api/analyze",
                data={"lead_tenant": "Lead Tenant", "property_address": "Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Gemini is busy", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
