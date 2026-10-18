import unittest

from fastapi.testclient import TestClient

from app import bills


class BillsEstimatorTests(unittest.TestCase):
    def test_extract_postcode_standard_formats(self):
        self.assertEqual(bills.extract_postcode("10 Downing Street, London SW1A 2AA"), "SW1A 2AA")
        self.assertEqual(bills.extract_postcode("Flat 4, 12 Wilmslow Road, Manchester M14 6TP"), "M14 6TP")
        self.assertEqual(bills.extract_postcode("Bristol BS8 1TH"), "BS8 1TH")
        self.assertEqual(bills.extract_postcode("No postcode provided in this address"), None)

    def test_get_region_mapping(self):
        self.assertEqual(bills.get_region("SW1A 2AA"), "London")
        self.assertEqual(bills.get_region("M14 6TP"), "North West")
        self.assertEqual(bills.get_region("LS2 9JT"), "Yorkshire")
        self.assertEqual(bills.get_region("EH1 1YZ"), "Scotland")
        self.assertEqual(bills.get_region("UNKNOWN"), "UK Average")
        self.assertIsNone(bills.extract_postcode("This address mentions M14 but has no full postcode"))

    def test_student_council_tax_exemption_zeroes_cost(self):
        result_student = bills.estimate_bills("10 Oxford Road, Manchester M13 9PL", tenant_count=3, is_student=True)
        council_tax_bill = next(b for b in result_student["bills"] if b["id"] == "council_tax")
        self.assertEqual(council_tax_bill["monthly_cost"], 0.0)
        self.assertTrue(result_student["totals"]["student_savings_monthly"] > 100.0)

        result_non_student = bills.estimate_bills("10 Oxford Road, Manchester M13 9PL", tenant_count=3, is_student=False)
        council_tax_non_student = next(b for b in result_non_student["bills"] if b["id"] == "council_tax")
        self.assertTrue(council_tax_non_student["monthly_cost"] > 100.0)
        self.assertEqual(
            bills.estimate_bills("M14 6TP")["bills"][3]["monthly_cost"],
            bills.estimate_bills("M14 6TP")["bills"][3]["standard_cost"],
        )

    def test_detect_included_services_from_need_to_knows(self):
        need_to_knows = [
            {"topic": "Utilities", "clause_ref": "3.1", "text": "Water rates and broadband are included in the monthly rent."},
        ]
        included = bills.detect_included_services(need_to_knows)
        self.assertTrue(included["water"])
        self.assertTrue(included["broadband"])
        self.assertFalse(included["energy"])

    def test_detect_included_services_does_not_infer_unmentioned_bills(self):
        included = bills.detect_included_services([
            {"topic": "Utilities", "clause_ref": "3", "text": "Water and Wi-Fi are included. Gas and electricity are paid separately."},
        ])
        self.assertTrue(included["water"])
        self.assertTrue(included["broadband"])
        self.assertFalse(included["energy"])
        self.assertFalse(included["council_tax"])

    def test_cap_limited_utility_is_not_assumed_fully_included(self):
        included = bills.detect_included_services([
            {"topic": "Utilities", "clause_ref": "3", "text": "Water and broadband included subject to a £35 fair usage cap."},
        ])
        self.assertFalse(included["water"])
        self.assertFalse(included["broadband"])

    def test_included_service_detection_can_be_overridden(self):
        result = bills.estimate_bills(
            "Manchester M14 6TP",
            need_to_knows=[
                {"topic": "Utilities", "clause_ref": "3", "text": "Water is included in rent."},
            ],
            included_overrides={"water": False},
        )
        water = next(item for item in result["bills"] if item["id"] == "water")
        self.assertFalse(water["included_in_rent"])
        self.assertTrue(water["active"])

    def test_bill_selections_persist_when_estimates_are_recomputed(self):
        result = bills.estimate_bills(
            "Manchester M14 6TP",
            tenant_count=3,
            bill_selections={"energy": False, "water": True, "parking": True},
        )
        selected = {bill["id"]: bill["active"] for bill in result["bills"]}
        self.assertFalse(selected["energy"])
        self.assertTrue(selected["water"])
        self.assertTrue(selected["parking"])
        expected_total = sum(
            bill["monthly_cost"]
            for bill in result["bills"]
            if bill["active"] and not bill["included_in_rent"]
        )
        self.assertEqual(result["totals"]["total_monthly"], round(expected_total, 2))

    def test_epc_does_not_claim_a_made_up_property_rating(self):
        epc = bills.estimate_epc()
        self.assertFalse(epc["available"])
        self.assertNotIn("rating", epc)

    def test_per_person_split_divides_evenly(self):
        result = bills.estimate_bills("High Street, Birmingham B15 2TT", tenant_count=4, is_student=True)
        self.assertEqual(
            result["totals"]["per_person_monthly"],
            round(result["totals"]["total_monthly"] / 4, 2),
        )

    def test_bills_endpoint_returns_json_estimate(self):
        from main import app

        client = TestClient(app)
        res = client.post(
            "/api/bills/estimate",
            json={
                "property_address": "45 Hollow Way, Oxford OX4 2NH",
                "tenant_count": 3,
                "is_student": True,
                "bill_selections": {"energy": False, "water": True},
                "lead_tenant": "Lead Tenant",
                "co_tenants": ["Co Tenant 1", "Co Tenant 2"],
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["region"], "South East")
        self.assertEqual(data["tenant_count"], 3)
        self.assertEqual(data["is_student"], True)
        active_bills = {item["id"]: item["active"] for item in data["bills"]}
        self.assertFalse(active_bills["energy"])
        self.assertTrue(active_bills["water"])
        self.assertIn("epc", data)
        self.assertIn("bills", data)
        self.assertIn("totals", data)
        self.assertEqual(data["totals"]["student_savings_monthly"], 162.4)
        self.assertFalse(data["live_data_available"])

    def test_endpoint_rejects_invalid_household_size(self):
        from main import app

        response = TestClient(app).post(
            "/api/bills/estimate",
            json={"property_address": "M14 6TP", "tenant_count": 0},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
