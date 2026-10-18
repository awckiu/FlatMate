import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import cases
from main import app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

RESULT = {
    "pages": 2,
    "summary": "A 12-month assured shorthold tenancy.",
    "need_to_knows": [{"topic": "Guests", "clause_ref": "4.2", "text": "Guests may stay up to 7 nights."}],
    "flags": [
        {
            "kind": "contradicts_law",
            "clause_ref": "7",
            "clause_excerpt": "a £50 admin fee",
            "explanation": "Admin fees are banned.",
            "citation": {"source": "Tenant Fees Act 2019", "section": "s.1", "url": "https://example.test/tfa"},
        },
        {
            "kind": "clarify",
            "clause_ref": "99",
            "clause_excerpt": "reasonable wear",
            "explanation": "Ask what counts as reasonable.",
            "citation": None,
        },
    ],
    "email": "Dear landlord, ...",
    "mode": "gemini",
    "law_data_available": True,
    "disclaimer": "This tool provides information, not legal advice.",
}
CLAUSES = [
    {"clause_ref": "4.2", "text": "4.2 Guests may stay for no more than seven nights."},
    {"clause_ref": "7", "text": "7 The tenant must pay a £50 admin fee on signing."},
]


class CaseCodeTests(unittest.TestCase):
    def test_codes_use_the_readable_alphabet(self):
        code = cases.new_code()
        self.assertTrue(code.startswith("FLAT-"))
        self.assertEqual(len(code), len("FLAT-") + cases.CODE_LENGTH)
        self.assertTrue(set(code[5:]) <= set(cases.CODE_ALPHABET))

    def test_normalize_code_accepts_lowercase_and_a_missing_prefix(self):
        self.assertEqual(cases.normalize_code(" flat-ab2cd3 "), "FLAT-AB2CD3")
        self.assertEqual(cases.normalize_code("ab2cd3"), "FLAT-AB2CD3")

    def test_get_case_without_a_database_returns_503(self):
        with patch.object(cases.config, "DATABASE_URL", None):
            response = TestClient(app).get("/api/cases/FLAT-AB2CD3")
        self.assertEqual(response.status_code, 503)


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class SavedCaseTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(cases.config, "DATABASE_URL", TEST_DATABASE_URL)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self):
        with cases.connect() as conn:
            return cases.save_case(
                conn,
                lead_tenant="Jane Doe",
                co_tenants=["Sam Lee"],
                property_address="1 Example Street",
                filename="agreement.pdf",
                agreement_text="Full agreement text",
                clauses=CLAUSES,
                result=RESULT,
            )

    def test_a_saved_case_reads_back_like_the_analysis(self):
        code = self.save()
        with cases.connect() as conn:
            case = cases.get_case(conn, code)

        self.assertEqual(case["case_code"], code)
        self.assertEqual(case["tenants"], [
            {"name": "Jane Doe", "role": "lead"},
            {"name": "Sam Lee", "role": "co_tenant"},
        ])
        for field in ("pages", "summary", "need_to_knows", "flags", "email", "mode", "law_data_available"):
            self.assertEqual(case[field], RESULT[field], field)

    def test_two_cases_get_different_codes(self):
        self.assertNotEqual(self.save(), self.save())

    def test_analyze_saves_the_case_and_it_can_be_reopened(self):
        client = TestClient(app)
        result = {**RESULT, "agreement_text": "Full agreement text", "clauses": CLAUSES}
        with patch("main.pipeline.analyze", return_value=result):
            response = client.post(
                "/api/analyze",
                data={"lead_tenant": "Jane Doe", "property_address": "1 Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            )
        body = response.json()
        self.assertTrue(body["saved"])
        self.assertNotIn("agreement_text", body)

        reopened = client.get("/api/cases/" + body["case_code"].lower())
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["flags"], RESULT["flags"])

        self.assertEqual(reopened.json()["pdf_url"], body["pdf_url"])
        pdf = client.get(body["pdf_url"])
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertEqual(pdf.content, b"%PDF-test")

    def test_a_case_saved_without_its_pdf_has_no_pdf_link(self):
        code = self.save()
        with cases.connect() as conn:
            self.assertIsNone(cases.get_case(conn, code)["pdf_url"])
        self.assertEqual(TestClient(app).get(f"/api/cases/{code}/agreement.pdf").status_code, 404)

    def test_an_unknown_code_returns_404(self):
        response = TestClient(app).get("/api/cases/FLAT-ZZZZZZ")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
