"""Illustrative UK household bill estimates and student council-tax draft support."""

from __future__ import annotations

import re
from typing import Any

# Match complete UK postcodes so ordinary address words aren't mistaken for outcodes.
POSTCODE_PATTERN = re.compile(
    r"\b(GIR\s?0AA|(?:[A-PR-UWYZ][0-9][0-9A-HJKPSTUW]?|"
    r"[A-PR-UWYZ][A-HK-Y][0-9][0-9ABEHMNPRVWXY]?)\s?"
    r"[0-9][ABD-HJLNP-UW-Z]{2})\b",
    re.IGNORECASE,
)

# Regional mapping from UK postcode areas (outward prefixes)
REGION_MAP = {
    # London
    ("E", "EC", "N", "NW", "SE", "SW", "W", "WC"): "London",
    # South East
    ("BR", "CR", "DA", "EN", "HA", "IG", "KT", "RM", "SM", "TW", "UB", "OX", "RG", "BN", "SO", "PO", "ME", "CT", "CM", "SS"): "South East",
    # South West
    ("BS", "BA", "EX", "PL", "TR", "TQ", "GL", "SN", "DT", "BH", "SP"): "South West",
    # Midlands
    ("B", "CV", "DY", "WS", "WV", "LE", "NG", "DE", "NN", "ST", "TF", "WR"): "Midlands",
    # North West
    ("M", "L", "SK", "WA", "WN", "BL", "PR", "BB", "LA", "FY", "CH", "CW"): "North West",
    # Yorkshire & The Humber
    ("LS", "BD", "HD", "HX", "WF", "S", "YO", "HG", "HU", "DN"): "Yorkshire",
    # North East
    ("NE", "SR", "DH", "TS", "DL"): "North East",
    # Scotland
    ("EH", "G", "AB", "DD", "FK", "IV", "KW", "PA", "PH", "KY", "ML", "DG", "TD"): "Scotland",
    # Wales
    ("CF", "SA", "NP", "LL", "LD", "SY"): "Wales",
}

# Broad, illustrative regional multipliers; these are not sourced live tariffs.
REGIONAL_TARIFF_FACTORS: dict[str, dict[str, float]] = {
    "London": {"energy": 1.05, "water": 0.95, "council_tax": 1.15},
    "South East": {"energy": 1.04, "water": 1.05, "council_tax": 1.12},
    "South West": {"energy": 1.02, "water": 1.20, "council_tax": 1.08},
    "Midlands": {"energy": 0.98, "water": 0.96, "council_tax": 1.02},
    "North West": {"energy": 0.97, "water": 1.02, "council_tax": 1.05},
    "Yorkshire": {"energy": 0.96, "water": 0.98, "council_tax": 1.04},
    "North East": {"energy": 0.95, "water": 0.97, "council_tax": 1.06},
    "Scotland": {"energy": 1.02, "water": 0.90, "council_tax": 0.95},
    "Wales": {"energy": 1.01, "water": 1.10, "council_tax": 1.00},
    "UK Average": {"energy": 1.00, "water": 1.00, "council_tax": 1.00},
}


def extract_postcode(text: str) -> str | None:
    """Extract standard UK postcode from an address string."""
    if not text:
        return None
    match = POSTCODE_PATTERN.search(text.upper())
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def get_region(postcode: str | None) -> str:
    """Map UK postcode outward code to a broader administrative region."""
    if not postcode:
        return "UK Average"
    outcode = postcode.strip().split()[0].upper()
    area_alpha = re.match(r"^[A-Z]+", outcode)
    if not area_alpha:
        return "UK Average"
    alpha_code = area_alpha.group(0)

    for prefixes, region in REGION_MAP.items():
        if alpha_code in prefixes:
            return region
    return "UK Average"


def detect_included_services(need_to_knows: list[dict[str, str]] | None) -> dict[str, bool]:
    """Find explicit inclusion statements without cross-matching other services."""
    included = {
        "energy": False,
        "water": False,
        "broadband": False,
        "council_tax": False,
    }
    if not need_to_knows:
        return included

    services = {
        "energy": re.compile(r"\b(?:gas|electric(?:ity)?|heating|energy)\b"),
        "water": re.compile(r"\b(?:water|sewerage)\b"),
        "broadband": re.compile(r"\b(?:broadband|internet|wi[\s-]?fi)\b"),
        "council_tax": re.compile(r"\bcouncil\s+tax\b"),
    }
    inclusion = re.compile(
        r"\b(?:included|inclusive|landlord\s+pays|provided\s+by\s+landlord|"
        r"no\s+extra\s+charge|free)\b"
    )
    exclusion = re.compile(
        r"\b(?:not\s+included|excluded|tenant(?:s)?\s+(?:is|are)?\s*"
        r"responsible|paid\s+separately|charged\s+separately|at\s+tenant'?s\s+expense|"
        r"subject\s+to\s+(?:a\s+)?(?:£?\d+(?:\.\d+)?\s*)?"
        r"(?:cap|limit|fair[- ]usage))\b"
    )

    for item in need_to_knows:
        text = f"{item.get('topic', '')}: {item.get('text', '')}".lower()
        for clause in re.split(r"\b(?:but|while|whereas|however)\b|[;.!?]", text):
            if not inclusion.search(clause) or exclusion.search(clause):
                continue
            for service, pattern in services.items():
                if pattern.search(clause):
                    included[service] = True

    return included


def estimate_epc() -> dict[str, Any]:
    """Return an unavailable state until a property-level EPC source is connected."""
    return {
        "available": False,
        "description": "Property-specific EPC data is not connected. Search the official EPC register.",
        "search_url": "https://www.gov.uk/find-energy-certificate",
    }


def generate_council_tax_letter(lead_tenant: str, co_tenants: list[str], address: str) -> str:
    """Generate a draft asking the council to confirm any applicable student exemption."""
    from datetime import date

    all_tenants = [lead_tenant, *co_tenants]
    tenant_list = "\n".join(f"  - {name}" for name in all_tenants if name)

    return f"""To: The Council Tax Department / Local Revenues Billing Authority
Property Address: {address}
Date: {date.today().strftime("%d %B %Y")}

RE: REQUEST TO CHECK COUNCIL TAX STUDENT EXEMPTION ELIGIBILITY

Dear Sir / Madam,

We are the tenants at the property above and would be grateful if you could confirm whether the property qualifies for a student council tax exemption or discount.

The tenants are:
{tenant_list}

If relevant, we can provide current student-status certificates and any other evidence you require. Please let us know what information is needed and whether an exemption or discount can be applied to this account.

Thank you for your prompt assistance.

Yours sincerely,

{lead_tenant} (On behalf of the listed tenants)
{address}
"""


def estimate_bills(
    property_address: str,
    tenant_count: int = 1,
    need_to_knows: list[dict[str, str]] | None = None,
    is_student: bool = False,
    bill_selections: dict[str, bool] | None = None,
    lead_tenant: str = "Lead Tenant",
    co_tenants: list[str] | None = None,
    included_overrides: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Calculate illustrative bills; student status means all household members qualify."""
    if tenant_count < 1:
        raise ValueError("tenant_count must be at least 1")
    postcode = extract_postcode(property_address)
    region = get_region(postcode)
    regional_factors = REGIONAL_TARIFF_FACTORS.get(region, REGIONAL_TARIFF_FACTORS["UK Average"])

    included_flags = detect_included_services(need_to_knows)
    for service_id, is_included in (included_overrides or {}).items():
        if service_id in included_flags:
            included_flags[service_id] = is_included
    selections = bill_selections or {}

    # The following fixed allowances are illustrative, not live provider tariffs.
    base_energy = (85.0 + (tenant_count - 1) * 24.0) * regional_factors["energy"]
    energy_cost = round(base_energy, 2)

    # Broad regional weighting is only a planning assumption.
    base_water = (28.0 + (tenant_count - 1) * 8.5) * regional_factors["water"]
    water_cost = round(base_water, 2)

    # 3. High-Speed Student Broadband (split across home)
    broadband_cost = 30.00

    # This generic Band B/C planning allowance is not a property bill quote.
    raw_council_tax = round(145.00 * regional_factors["council_tax"], 2)
    council_tax_cost = 0.00 if is_student else raw_council_tax
    student_savings = (
        raw_council_tax
        if is_student and not included_flags["council_tax"]
        else 0.00
    )

    # 5. Optional Parking & Bike Storage
    parking_cost = 12.00
    bike_storage_cost = 0.00

    bills_list = [
        {
            "id": "energy",
            "name": "Electricity & Gas",
            "category": "utilities",
            "icon": "fa-bolt",
            "monthly_cost": energy_cost,
            "included_in_rent": included_flags["energy"],
            "active": not included_flags["energy"],
            "description": f"Illustrative energy allowance for {region}; not a live Ofgem tariff.",
        },
        {
            "id": "water",
            "name": "Water & Sewerage",
            "category": "utilities",
            "icon": "fa-droplet",
            "monthly_cost": water_cost,
            "included_in_rent": included_flags["water"],
            "active": not included_flags["water"],
            "description": f"Illustrative water allowance for {region}; check your supplier for actual rates.",
        },
        {
            "id": "broadband",
            "name": "Superfast Wi-Fi",
            "category": "connectivity",
            "icon": "fa-wifi",
            "monthly_cost": broadband_cost,
            "included_in_rent": included_flags["broadband"],
            "active": not included_flags["broadband"],
            "description": "Illustrative broadband allowance; check provider prices and contract terms.",
        },
        {
            "id": "council_tax",
            "name": "Council Tax",
            "category": "statutory",
            "icon": "fa-graduation-cap",
            "monthly_cost": council_tax_cost,
            "included_in_rent": included_flags["council_tax"],
            "active": not included_flags["council_tax"],
            "is_student_exempt": is_student,
            "standard_cost": raw_council_tax,
            "description": (
                "Estimated at £0 only if all residents qualify; confirm with your council."
                if is_student
                else f"Illustrative Band B/C allowance for {region}; check the council bill."
            ),
        },
        {
            "id": "parking",
            "name": "Resident Parking Permit",
            "category": "optional",
            "icon": "fa-car",
            "monthly_cost": parking_cost,
            "included_in_rent": False,
            "active": False,  # optional, off by default
            "description": "Local council on-street parking permit (optional).",
        },
        {
            "id": "bike",
            "name": "Secure Bike Storage",
            "category": "optional",
            "icon": "fa-bicycle",
            "monthly_cost": bike_storage_cost,
            "included_in_rent": False,
            "active": False,  # optional, off by default
            "description": "On-site bike rack or municipal bicycle locker.",
        },
    ]

    for bill in bills_list:
        bill_id = bill["id"]
        default_active = not bill["included_in_rent"] and bill_id not in {"parking", "bike"}
        bill["active"] = (
            bool(selections[bill_id])
            if bill_id in selections and not bill["included_in_rent"]
            else default_active
        )

    active_bills = [b for b in bills_list if b["active"] and not b["included_in_rent"]]
    total_monthly = round(sum(b["monthly_cost"] for b in active_bills), 2)
    per_person_monthly = round(total_monthly / tenant_count, 2)

    epc_info = estimate_epc()
    exemption_letter = (
        generate_council_tax_letter(lead_tenant, co_tenants or [], property_address)
        if is_student
        else ""
    )

    return {
        "property_address": property_address,
        "postcode": postcode or "Unspecified",
        "region": region,
        "tenant_count": tenant_count,
        "is_student": is_student,
        "estimate_basis": (
            "Illustrative planning allowances with broad regional adjustments; "
            "not live supplier tariffs, water-company rates, council-tax bills, or EPC data."
        ),
        "live_data_available": False,
        "epc": epc_info,
        "bills": bills_list,
        "totals": {
            "total_monthly": total_monthly,
            "per_person_monthly": per_person_monthly,
            "student_savings_monthly": student_savings,
            "currency": "GBP",
        },
        "council_tax_letter": exemption_letter,
        "reminders": {
            "meter_reading_day": 1,
            "recommendation": "Submit gas & electricity meter readings on the 1st of every month to avoid estimated bills.",
        },
    }
