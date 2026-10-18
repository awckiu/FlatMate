"""The only law sources the app may ingest (agreed with the team, 2026-10-18).

Everything comes from two official publishers: legislation.gov.uk (revised Acts and
regulations) and gov.uk (government guidance). scripts/fetch_law.py downloads exactly
these, and scripts/ingest_law.py refuses any file that doesn't match an entry here.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_HOSTS = {"www.legislation.gov.uk", "www.gov.uk"}


@dataclass(frozen=True)
class TrustedSource:
    stem: str  # file name in backend/data/law/<stem>.txt
    name: str  # stored as law_sources.name and cited by the LLM
    url: str
    kind: str  # "legislation" or "guidance"
    # Legislation only: which parts to keep, e.g. "s.4A-4B", "s.16", "Sch 2".
    keep: tuple[str, ...] = ()
    label: str = "s."  # how sections are cited: "s." for Acts, "reg " for regulations


TRUSTED_SOURCES = [
    TrustedSource(
        "housing_act_1988",
        "Housing Act 1988",
        "https://www.legislation.gov.uk/ukpga/1988/50",
        "legislation",
        ("s.1", "s.4A-4B", "s.5", "s.7-8", "s.13-14ZC", "s.15", "s.16-16P", "Sch 2"),
    ),
    TrustedSource(
        "housing_act_2004",
        "Housing Act 2004",
        "https://www.legislation.gov.uk/ukpga/2004/34",
        "legislation",
        ("s.1-2", "s.4", "s.55", "s.61", "s.67", "s.72-73", "s.75", "s.212-215C", "s.254", "Sch 4"),
    ),
    TrustedSource(
        "tenant_fees_act_2019",
        "Tenant Fees Act 2019",
        "https://www.legislation.gov.uk/ukpga/2019/4",
        "legislation",
        ("s.1-5B", "s.17", "Sch 1", "Sch 2"),
    ),
    TrustedSource(
        "renters_rights_act_2025",
        "Renters' Rights Act 2025",
        "https://www.legislation.gov.uk/ukpga/2025/26",
        "legislation",
        # Most of this Act amends the Housing Act 1988 (kept above in its revised form);
        # these are the parts that stand on their own.
        ("s.33-35", "s.41-42", "s.56", "s.58", "s.82", "s.90", "s.146-147"),
    ),
    TrustedSource(
        "landlord_and_tenant_act_1985",
        "Landlord and Tenant Act 1985",
        "https://www.legislation.gov.uk/ukpga/1985/70",
        "legislation",
        # s.8 is Wales-only, s.9 covers agricultural workers and s.10A-10B social housing.
        ("s.1-3", "s.9A-9C", "s.10", "s.11-14"),
    ),
    TrustedSource(
        "protection_from_eviction_act_1977",
        "Protection from Eviction Act 1977",
        "https://www.legislation.gov.uk/ukpga/1977/43",
        "legislation",
        ("s.1-3A", "s.5"),
    ),
    TrustedSource(
        "hmo_management_regulations_2006",
        "Management of Houses in Multiple Occupation (England) Regulations 2006",
        "https://www.legislation.gov.uk/uksi/2006/372",
        "legislation",
        ("reg 3-10",),
        "reg ",
    ),
    TrustedSource(
        "deposit_protection",
        "Tenancy deposit protection (gov.uk)",
        "https://www.gov.uk/tenancy-deposit-protection",
        "guidance",
    ),
    TrustedSource(
        "how_to_rent",
        "How to rent (gov.uk)",
        "https://www.gov.uk/government/publications/how-to-rent",
        "guidance",
    ),
]


def is_trusted_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
