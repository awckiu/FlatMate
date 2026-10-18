# Law sources

Only these trusted sources are used (the list lives in `backend/app/rag/sources.py`):

| File | Source | Publisher |
|---|---|---|
| `housing_act_1988.txt` | Housing Act 1988 (revised, includes Renters' Rights Act changes) | legislation.gov.uk |
| `housing_act_2004.txt` | Housing Act 2004 (hazards, HMO licensing, deposit protection) | legislation.gov.uk |
| `tenant_fees_act_2019.txt` | Tenant Fees Act 2019 | legislation.gov.uk |
| `renters_rights_act_2025.txt` | Renters' Rights Act 2025 (parts that don't just amend the 1988 Act) | legislation.gov.uk |
| `landlord_and_tenant_act_1985.txt` | Landlord and Tenant Act 1985 (landlord details, repairs, fitness) | legislation.gov.uk |
| `protection_from_eviction_act_1977.txt` | Protection from Eviction Act 1977 (unlawful eviction, harassment, notice to quit) | legislation.gov.uk |
| `deposit_protection.txt` | Tenancy deposit protection | gov.uk |
| `how_to_rent.txt` | How to rent: the checklist for renting in England | gov.uk |

The files are generated, never hand-edited. From `backend/`:

    .venv\Scripts\python -m scripts.fetch_law     # download from legislation.gov.uk / gov.uk
    .venv\Scripts\python -m scripts.ingest_law    # embed and store (Tiger Data if DATABASE_URL is set)

`fetch_law` refuses any URL outside those two sites, and `ingest_law` refuses a file whose
header or section links don't match the list. To change what is kept from an Act, edit its
`keep` list in `sources.py` (`--list <stem>` prints every section of an Act) and re-run both.

File format:

    source: Tenant Fees Act 2019
    url: https://www.legislation.gov.uk/ukpga/2019/4
    last_updated: 2026-06-19

    ### s.1 | Prohibitions applying to landlords | https://www.legislation.gov.uk/ukpga/2019/4/section/1
    (1) A landlord must not require a relevant person to make a prohibited payment ...

Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
