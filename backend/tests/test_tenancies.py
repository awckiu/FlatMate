import copy
import tempfile
import uuid
from contextlib import nullcontext
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from app import calendar as tenancy_calendar
from app import config, tenancies
from main import app, optional_account


class TenancyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "flatmate.sqlite3"
        self.database_patch = patch.object(config, "REVIEWS_DB_PATH", self.database_path)
        self.url_patch = patch.object(config, "DATABASE_URL", None)
        self.photo_patch = patch.object(config, "PHOTO_STORAGE_DIR", Path(self.temp_dir.name) / "photos")
        self.database_patch.start()
        self.url_patch.start()
        self.photo_patch.start()
        # Logins come from app.auth (Postgres); here each client names its account in a header.
        self.accounts = {}
        self.cases = {}

        def test_account(request: Request):
            account = self.accounts.get(request.headers.get("x-test-account", ""))
            if account:
                tenancies.sync_account(account)
            return account

        app.dependency_overrides[optional_account] = test_account
        self.case_patches = [
            patch("main.cases.connect", side_effect=lambda: nullcontext()),
            patch("main.cases.get_case", side_effect=lambda _conn, code: copy.deepcopy(self.cases.get(code))),
        ]
        for case_patch in self.case_patches:
            case_patch.start()
        self.client = TestClient(app)
        self.account = self.register(self.client, "lead@example.test", "Lead Tenant")

    def tearDown(self):
        self.client.close()
        for case_patch in self.case_patches:
            case_patch.stop()
        app.dependency_overrides.pop(optional_account, None)
        self.url_patch.stop()
        self.photo_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def register(self, client, email, name):
        account = {"id": str(uuid.uuid4()), "email": email, "display_name": name}
        self.accounts[account["id"]] = account
        client.headers["x-test-account"] = account["id"]
        return account

    def create_profile(
        self,
        client=None,
        *,
        signed=True,
        tenancy_start=None,
        household_mode="friends",
        role="lead",
        current_tenant="Lead Tenant",
        lead_tenant="Lead Tenant",
        co_tenants=None,
    ):
        client = client or self.client
        tenant_names = ["Friend Tenant"] if co_tenants is None else co_tenants
        case_code = f"FLAT-{len(self.cases) + 2:06d}"
        self.cases[case_code] = {
            "case_code": case_code,
            "lead_tenant": lead_tenant,
            "co_tenants": tenant_names,
            "property_address": "10 Example Road, Leeds LS1 2AB",
            "filename": "agreement.pdf",
            "summary": "Quiet enjoyment and repair details.",
            "need_to_knows": [{"topic": "Repairs", "clause_ref": "4", "text": "Report urgent leaks promptly."}],
            "flags": [],
        }
        body = {
            "household_mode": household_mode,
            "hmo_status": "unknown" if household_mode != "single" else "not_applicable",
            "property_address": "10 Example Road, Leeds LS1 2AB",
            "lead_tenant": lead_tenant,
            "co_tenants": tenant_names,
            "current_tenant": current_tenant,
            "current_role": role,
            "agreement_signed": signed,
            "signed_on": date.today().isoformat() if signed else None,
            "tenancy_start": tenancy_start or date.today().isoformat(),
            "case_code": case_code,
        }
        return client.post("/api/tenancies", json=body)

    def test_existing_profile_table_is_migrated_with_review_id(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with patch.object(config, "REVIEWS_DB_PATH", legacy_path):
            connection = tenancies._connection()
            try:
                connection.execute(
                    """
                    CREATE TABLE tenancy_profiles (
                        id text PRIMARY KEY,
                        code text,
                        owner_account_id text NOT NULL,
                        household_mode text NOT NULL,
                        hmo_status text NOT NULL,
                        property_address text NOT NULL,
                        lead_tenant text NOT NULL,
                        co_tenants text NOT NULL,
                        current_tenant text NOT NULL,
                        "current_role" text NOT NULL,
                        agreement_signed integer NOT NULL DEFAULT 0,
                        signed_on text,
                        tenancy_start text,
                        is_active integer NOT NULL DEFAULT 0,
                        review text NOT NULL,
                        created_at text NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tenancy_profiles
                        (id, owner_account_id, household_mode, hmo_status, property_address,
                         lead_tenant, co_tenants, current_tenant, "current_role", review, created_at)
                    VALUES ('legacy-profile', ?, 'single', 'not_applicable', '1 Example Road',
                            'Lead Tenant', '[]', 'Lead Tenant', 'lead', '{}', '2026-01-01')
                    """,
                    (self.account["id"],),
                )

                tenancies._ensure_schema(connection)

                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(tenancy_profiles)").fetchall()
                }
                legacy_profile = connection.execute(
                    "SELECT id, review_id FROM tenancy_profiles WHERE id = 'legacy-profile'"
                ).fetchone()
            finally:
                connection.close()

        self.assertIn("review_id", columns)
        self.assertEqual(legacy_profile["id"], "legacy-profile")
        self.assertIsNone(legacy_profile["review_id"])

    def test_profile_code_unlocks_on_start_and_co_tenants_share_record(self):
        response = self.create_profile()

        self.assertEqual(response.status_code, 201, response.text)
        profile = response.json()
        self.assertTrue(profile["is_active"])
        self.assertRegex(profile["code"], r"^HOME-[A-HJ-NP-Z2-9]{10}$")
        self.assertIn("need_to_knows", profile["review"])

        friend = TestClient(app)
        try:
            self.register(friend, "friend@example.test", "Friend Tenant")
            joined = friend.post(
                "/api/tenancies/join",
                json={
                    "code": profile["code"],
                    "tenant_name": "Friend Tenant",
                    "role": "co_tenant",
                },
            )
        finally:
            friend.close()

        self.assertEqual(joined.status_code, 200, joined.text)
        self.assertEqual(joined.json()["id"], profile["id"])
        self.assertEqual(joined.json()["role"], "co_tenant")

    def test_future_tenancy_has_no_code_until_start_date(self):
        future_start = (date.today() + timedelta(days=3)).isoformat()
        response = self.create_profile(tenancy_start=future_start)
        self.assertEqual(response.status_code, 201, response.text)
        profile = response.json()
        self.assertFalse(profile["is_active"])
        self.assertIsNone(profile["code"])

        activated = self.client.patch(
            f"/api/tenancies/{profile['id']}/signing",
            json={
                "agreement_signed": True,
                "signed_on": date.today().isoformat(),
                "tenancy_start": date.today().isoformat(),
            },
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertTrue(activated.json()["is_active"])
        self.assertIsNotNone(activated.json()["code"])

    def test_only_one_tenancy_is_current_for_an_account(self):
        first = self.create_profile()
        second = self.create_profile(
            household_mode="single",
            current_tenant="Solo Tenant",
            lead_tenant="Solo Tenant",
            co_tenants=[],
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)

        profiles = self.client.get("/api/tenancies").json()
        current_profiles = [profile for profile in profiles if profile["is_current"]]
        self.assertEqual(len(current_profiles), 1)
        self.assertEqual(current_profiles[0]["id"], second.json()["id"])

    def test_family_household_setup_is_rejected(self):
        response = self.create_profile(household_mode="family")
        self.assertEqual(response.status_code, 422)

    def test_chat_is_locked_until_signing_and_photo_is_locked_until_start(self):
        not_signed = self.create_profile(signed=False)
        self.assertEqual(not_signed.status_code, 201, not_signed.text)
        profile = not_signed.json()
        self.assertEqual(
            self.client.get(f"/api/tenancies/{profile['id']}/chat").status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                f"/api/tenancies/{profile['id']}/photos",
                data={"room": "Kitchen"},
                files={"photo": ("kitchen.jpg", b"\xff\xd8\xffsample", "image/jpeg")},
            ).status_code,
            409,
        )

    def test_photo_upload_retrieval_and_delete_are_member_scoped(self):
        profile = self.create_profile().json()
        upload = self.client.post(
            f"/api/tenancies/{profile['id']}/photos",
            data={"room": "Kitchen", "note": "Small mark by the sink"},
            files={"photo": ("kitchen.jpg", b"\xff\xd8\xffsample", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        photo_id = upload.json()["id"]

        photos = self.client.get(f"/api/tenancies/{profile['id']}/photos")
        self.assertEqual(photos.status_code, 200)
        self.assertEqual(photos.json()[0]["room"], "Kitchen")
        image = self.client.get(f"/api/tenancies/{profile['id']}/photos/{photo_id}")
        self.assertEqual(image.content, b"\xff\xd8\xffsample")

        other = TestClient(app)
        try:
            self.register(other, "other@example.test", "Other Tenant")
            self.assertEqual(
                other.get(f"/api/tenancies/{profile['id']}/photos/{photo_id}").status_code,
                404,
            )
        finally:
            other.close()

        deleted = self.client.delete(f"/api/tenancies/{profile['id']}/photos/{photo_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/tenancies/{profile['id']}/photos").json(), [])

    def test_calendar_monthly_and_one_off_events_and_member_access(self):
        profile = self.create_profile().json()
        first_of_month = date.today().replace(day=1)
        next_month = date(
            first_of_month.year + (1 if first_of_month.month == 12 else 0),
            1 if first_of_month.month == 12 else first_of_month.month + 1,
            1,
        )
        monthly_date = date(next_month.year, next_month.month, 5)
        one_off_date = date(next_month.year, next_month.month, 12)
        monthly = self.client.post(
            f"/api/tenancies/{profile['id']}/calendar",
            json={
                "title": "Rent",
                "kind": "rent",
                "due_date": monthly_date.isoformat(),
                "recurrence": "monthly",
                "amount": 800,
            },
        )
        one_off = self.client.post(
            f"/api/tenancies/{profile['id']}/calendar",
            json={
                "title": "Electricity",
                "kind": "bill",
                "due_date": one_off_date.isoformat(),
                "recurrence": "once",
                "amount": 45.5,
            },
        )
        self.assertEqual(monthly.status_code, 201, monthly.text)
        self.assertEqual(one_off.status_code, 201, one_off.text)

        scheduled_month = self.client.get(
            f"/api/tenancies/{profile['id']}/calendar?month={next_month:%Y-%m}"
        )
        self.assertEqual(scheduled_month.status_code, 200, scheduled_month.text)
        self.assertEqual(
            {event["title"] for event in scheduled_month.json()["events"]},
            {"Rent", "Electricity"},
        )
        self.assertFalse(scheduled_month.json()["email_reminders_enabled"])

        following_month = date(
            next_month.year + (1 if next_month.month == 12 else 0),
            1 if next_month.month == 12 else next_month.month + 1,
            1,
        )
        recurring_month = self.client.get(
            f"/api/tenancies/{profile['id']}/calendar?month={following_month:%Y-%m}"
        )
        self.assertEqual(
            [event["title"] for event in recurring_month.json()["events"]],
            ["Rent"],
        )

        other = TestClient(app)
        try:
            self.register(other, "stranger@example.test", "Stranger")
            denied = other.get(
                f"/api/tenancies/{profile['id']}/calendar?month={next_month:%Y-%m}"
            )
        finally:
            other.close()
        self.assertEqual(denied.status_code, 404)

        deleted = self.client.delete(
            f"/api/tenancies/{profile['id']}/calendar/{monthly.json()['id']}"
        )
        self.assertEqual(deleted.status_code, 204)

    def test_calendar_email_opt_in_requires_smtp_configuration(self):
        profile = self.create_profile().json()
        with patch.object(config, "EMAIL_REMINDERS_CONFIGURED", False):
            response = self.client.put(
                f"/api/tenancies/{profile['id']}/calendar/preferences",
                json={"enabled": True},
            )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(tenancy_calendar.get_email_preference(self.account["id"]))

    def test_calendar_email_reminder_sends_once_two_days_before_due(self):
        profile = self.create_profile().json()
        due_date = date.today() + timedelta(days=2)
        item = self.client.post(
            f"/api/tenancies/{profile['id']}/calendar",
            json={
                "title": "Rent",
                "kind": "rent",
                "due_date": due_date.isoformat(),
                "recurrence": "monthly",
                "amount": 800,
            },
        )
        self.assertEqual(item.status_code, 201, item.text)
        with (
            patch.object(config, "EMAIL_REMINDERS_CONFIGURED", True),
            patch.object(config, "REMINDER_FROM_EMAIL", "flatmate@example.test"),
            patch.object(tenancy_calendar, "_send_email") as send_email,
        ):
            tenancy_calendar.set_email_preference(self.account["id"], True)
            sent = tenancy_calendar.send_due_reminders(today=date.today())
            repeated_send_count = tenancy_calendar.send_due_reminders(today=date.today())

        self.assertEqual(sent, 1)
        self.assertEqual(repeated_send_count, 0)
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], self.account["email"])
        self.assertIn("Rent", send_email.call_args.args[2])

    def test_join_rejects_role_and_name_mismatch(self):
        profile = self.create_profile().json()
        friend = TestClient(app)
        try:
            self.register(friend, "wrong@example.test", "Friend Tenant")
            response = friend.post(
                "/api/tenancies/join",
                json={
                    "code": profile["code"],
                    "tenant_name": "Friend Tenant",
                    "role": "lead",
                },
            )
        finally:
            friend.close()
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
