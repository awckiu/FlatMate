import os
import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, cases
from main import app
from test_cases import CLAUSES, RESULT

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class PasswordTests(unittest.TestCase):
    def test_a_hash_verifies_only_its_own_password(self):
        stored = auth.hash_password("correct horse")
        self.assertNotIn("correct horse", stored)
        self.assertTrue(auth.verify_password("correct horse", stored))
        self.assertFalse(auth.verify_password("wrong horse", stored))

    def test_the_same_password_hashes_differently_each_time(self):
        self.assertNotEqual(auth.hash_password("correct horse"), auth.hash_password("correct horse"))

    def test_a_malformed_hash_does_not_verify(self):
        self.assertFalse(auth.verify_password("anything", "not-a-hash"))

    def test_me_without_logging_in_returns_401(self):
        self.assertEqual(TestClient(app).get("/api/me").status_code, 401)

    def test_signup_without_a_database_returns_503(self):
        with patch.object(auth.config, "DATABASE_URL", None):
            response = TestClient(app).post(
                "/api/auth/signup",
                json={"email": "a@example.test", "password": "long enough", "display_name": "A"},
            )
        self.assertEqual(response.status_code, 503)


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class AccountTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(cases.config, "DATABASE_URL", TEST_DATABASE_URL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.email = f"jane-{secrets.token_hex(4)}@example.test"

    def signup(self, client, email=None, name="Jane Doe", password="long enough"):
        return client.post(
            "/api/auth/signup", json={"email": email or self.email, "password": password, "display_name": name}
        )

    def analyze(self, client, lead="Jane Doe", co_tenants=""):
        result = {**RESULT, "agreement_text": "Full agreement text", "clauses": CLAUSES}
        with patch("main.pipeline.analyze", return_value=result):
            return client.post(
                "/api/analyze",
                data={"lead_tenant": lead, "co_tenants": co_tenants, "property_address": "1 Example Street"},
                files={"agreement": ("agreement.pdf", b"%PDF-test", "application/pdf")},
            ).json()

    def test_signup_logs_in_and_me_returns_the_user(self):
        client = TestClient(app)
        response = self.signup(client, email="  " + self.email.upper())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password_hash", response.json())
        me = client.get("/api/me").json()
        self.assertEqual((me["email"], me["display_name"]), (self.email, "Jane Doe"))

    def test_signup_rejects_a_short_password_and_a_taken_email(self):
        client = TestClient(app)
        self.assertEqual(self.signup(client, password="short").status_code, 422)
        self.assertEqual(self.signup(client).status_code, 200)
        self.assertEqual(self.signup(TestClient(app)).status_code, 409)

    def test_login_and_logout(self):
        self.signup(TestClient(app))
        client = TestClient(app)
        wrong = client.post("/api/auth/login", json={"email": self.email, "password": "not it at all"})
        self.assertEqual(wrong.status_code, 401)
        unknown = client.post("/api/auth/login", json={"email": "nobody@example.test", "password": "long enough"})
        self.assertEqual(unknown.json()["detail"], wrong.json()["detail"])

        self.assertEqual(client.post("/api/auth/login", json={"email": self.email, "password": "long enough"}).status_code, 200)
        self.assertEqual(client.get("/api/me").status_code, 200)
        client.post("/api/auth/logout")
        self.assertEqual(client.get("/api/me").status_code, 401)

    def test_an_analysis_while_logged_in_is_saved_to_the_account(self):
        client = TestClient(app)
        self.signup(client)
        code = self.analyze(client)["case_code"]
        mine = client.get("/api/my/cases").json()["cases"]
        self.assertEqual([(c["case_code"], c["role"]) for c in mine], [(code, "lead")])

    def test_a_guest_analysis_is_not_linked_to_anyone(self):
        code = self.analyze(TestClient(app))["case_code"]
        client = TestClient(app)
        self.signup(client)
        self.assertEqual(client.get("/api/my/cases").json()["cases"], [])
        self.assertTrue(code)

    def test_a_co_tenant_joins_with_the_code_and_claims_their_name(self):
        lead = TestClient(app)
        self.signup(lead)
        code = self.analyze(lead, co_tenants="Sam Lee")["case_code"]

        sam = TestClient(app)
        self.signup(sam, email="sam-" + self.email, name="sam lee")
        self.assertEqual(sam.post(f"/api/cases/{code.lower()}/join").status_code, 200)
        self.assertEqual(sam.post(f"/api/cases/{code}/join").status_code, 200)  # joining twice is fine
        self.assertEqual([(c["case_code"], c["role"]) for c in sam.get("/api/my/cases").json()["cases"]], [(code, "co_tenant")])
        # Sam took the existing co-tenant row rather than adding a third tenant.
        self.assertEqual(len(sam.get(f"/api/cases/{code}").json()["tenants"]), 2)

    def test_joining_an_unknown_code_returns_404_and_needs_a_login(self):
        self.assertEqual(TestClient(app).post("/api/cases/FLAT-ZZZZZZ/join").status_code, 401)
        client = TestClient(app)
        self.signup(client)
        self.assertEqual(client.post("/api/cases/FLAT-ZZZZZZ/join").status_code, 404)


if __name__ == "__main__":
    unittest.main()
