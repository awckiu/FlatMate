# FlatMate / Tenancy Demystifier: project plan

IKU Hackathon 2026, Society Track. 24 hours.

Status: **draft for the team to review**. Built from the Google Doc "IKU Hackathon" (the
source of truth), the latest CLAUDE.md draft (PR #2 branch, 2026-10-17 19:12) and the Tiger
Data schema draft v4 (db-schema-draft.md, in the project files; not in the repo yet). Anything marked **OPEN**
still needs a team decision.

---

## 1. What we are building (one paragraph)

An installable web app (website + phone app from one codebase) for students and people new to UK renting. You sign up, enter the tenant name(s)
and the property address, upload your tenancy agreement (PDF), and get back: the
need-to-knows people miss, flags on clauses that need clarifying with the landlord or that
contradict UK housing law (with the law cited), and a ready-to-copy email to the landlord.
The result is saved as a tenancy **case** with a case code; co-tenants join it with that code.
Later features (move-in photos, end-of-tenancy calendar, bills) build on the saved case.

### Demo story (what judges see)

1. Sign up, enter tenant name(s) + address, upload a sample agreement with planted problems
   (e.g. a banned fee, an unprotected deposit, a vague guest clause).
2. Click **Analyse agreement**.
3. See need-to-knows, "Questions to ask" cards and "Possible legal issues" cards, each legal
   card citing the Act and section next to the clause text.
4. Click **Copy email** to get the landlord email.
5. Show the saved case page and its case code; a second account joins with the code.
6. Open the same site on a phone, add it to the home screen, and show the case there.
7. If built: move-in photo upload from the phone camera, end-of-tenancy date calculator.

---

## 2. Decisions so far

| Topic | Decision | Source |
|---|---|---|
| LLM | Gemini API, **text only** (never on images). Default model `gemini-flash-latest` via `GEMINI_MODEL` | CLAUDE.md |
| Gemini calls | Whole agreement + retrieved law in **one** analysis call (batched only for very long agreements), then one call for the email. Never one call per clause | CLAUDE.md |
| Embeddings | Gemini embeddings at **768 dims** (pgvector HNSW index limit is 2000; scaffold currently uses 3072) | DB draft v4 |
| Law storage | Each law's sections stored **once** in Tiger Data with embeddings and the source's last-updated date. Re-ingest = delete source (cascades) and insert again | CLAUDE.md, DB draft v4 |
| Tiger Data | Postgres + pgvector for the law knowledge base **and** user/case data | CLAUDE.md |
| Vultr | Cloud Compute VM runs the app; Object Storage holds photo files (and optionally the agreement PDF) | CLAUDE.md, DB draft v4 |
| Login | **Email + password** for the MVP; Google sign-in later (`google_id` column ready) | Andrea, DB draft v4 |
| Co-tenants | Join a case by entering its **case code**; only linked users can open a case | Andrea, DB draft v4 |
| Relationships | Case ↔ users many-to-many (via tenants); case ↔ contract 1:1 (re-upload replaces); case ↔ email 1:1 (latest draft); case → photos 1:many; contract → clauses → flags 1:many | Andrea, DB draft v4 |
| Photos | Dated evidence only, no AI analysis. Listed under Later in CLAUDE.md, so a stretch goal | Andrea, CLAUDE.md |
| incident.io | **Future extension**, not in the MVP (section 11) | Andrea, CLAUDE.md |
| Website + app | **One installable web app (PWA)**: the website can be added to a phone's home screen and runs like an app. No separate native app in the 24h build | Andrea, 2026-10-17 |
| Email | Copy/paste only, no Gmail/Outlook integration | Google Doc |
| Disclaimer | Information, not legal advice; shown in the UI and the email | CLAUDE.md |
| Secrets | `.env` only (git-ignored); never committed, never pasted into chat or diffs | CLAUDE.md |
| Git workflow | **Ask before pushing**: show the full diff (or whole new file) in chat, get a team member's OK, then commit/push/PR | CLAUDE.md |

## 3. Open decisions (need the team)

- **OPEN-1 Tech stack.** CLAUDE.md's Stack section describes the scaffold: React 19 + Vite +
  TS frontend, FastAPI (Python) backend, FastAPI serving the built frontend on one URL.
  Confirm it's agreed; stack-specific notes below are tagged *(scaffold)*.
- **OPEN-2 Which law.** England only (Renters' Rights Act, Housing Acts) or all of the UK.
  Recommendation: England only for the demo.
- **OPEN-3 Store the agreement PDF** in Object Storage (`agreements.object_key`) or text only.
  Recommendation: text only for the demo; the column is optional.
- **OPEN-4 Design and branding** (separate thread, not discussed yet): product name (FlatMate
  from the Google Doc vs Tenancy Demystifier from the repo), logo and app icon, colours, fonts,
  tone of voice, UI look. Until it's decided, the frontend uses placeholder theme tokens
  (one file of colours/fonts) so branding drops in without rewrites. Needed early: the app
  icon and name feed the installable app (C1b) and the pitch slides (C10).

---

## 4. System overview

```
                  ┌───────────────────────── Vultr VM ──────────────────────────┐
 Browser ──HTTPS──▶  Frontend (web UI)  ──/api──▶  Backend API                   │
                  │                                 ├─▶ Auth (email + password, sessions)
                  │                                 │
                  │                                 ├─▶ Analysis pipeline ──▶ Gemini API
                  │                                 │     (PDF → text → clauses → retrieve law
                  │                                 │      → 1 analysis call → 1 email call)
                  │                                 │
                  │                                 ├─▶ Case store  ───────▶ Tiger Data (Postgres)
                  │                                 │                          • users, cases, tenants,
                  │                                 │                            agreements, clauses, flags,
                  │                                 │                            emails, photos (metadata)
                  │                                 ├─▶ Law retrieval ─────▶ Tiger Data (pgvector)
                  │                                 │                          • law_sources, law_sections
                  │                                 └─▶ Photo upload ──────▶ Vultr Object Storage
                  └────────────────────────────────────────────────────────────────┘
```

## 5. Components

Priority: **P0** = needed for the demo, **P1** = strengthens the demo, **P2** = only if
everything else works.

### C1. Frontend: agreement flow (P0)
The pages from the wireframe.
- **Header**: name + one-line explanation.
- **Agreement form**: lead tenant name, co-tenant name(s), property address (+ postcode),
  optional tenancy start/end dates, PDF upload.
- **Analyse** button with a loading state (analysis can take 10-30s).
- **Results**: need-to-knows list; "Questions to ask" cards (`clarify`); "Possible legal
  issues" cards (`contradicts_law`, cited Act + section); each card shows its clause text.
  "Information, not legal advice" disclaimer.
- **Landlord email**: draft text box with a **Copy email** button.
- Mobile-first layout (it doubles as the phone app). Built against mocked JSON (section 7) from hour 1.

### C1b. Installable app / PWA (P0)
- Web app manifest (name, icons, theme colour, `display: standalone`) so the site can be
  added to the home screen on Android and iOS.
- Service worker: cache the app shell so it opens instantly and shows an offline message
  instead of a blank page. API data stays online-only.
- HTTPS is required (C9 already provides it).
- Photo upload (C7) uses the phone camera through the file input (`capture`).
- Name, icons and theme colour come from design and branding (OPEN-4); placeholders until then.
- *(scaffold)* `vite-plugin-pwa` covers the manifest and service worker.

### C2. Frontend: accounts and cases (P0)
- Sign up / log in (email + password), log out.
- "My cases" list; open a case page showing the saved results (C1's results view).
- Case page shows the case code; **Join a case** form (enter code) for co-tenants.
- Later home for photos and the calendar; flags can be ticked as resolved (P1).

### C3. Backend API + auth (P0)
- `GET /api/health`
- Auth: `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`,
  `GET /api/me`. Passwords hashed with bcrypt/argon2; session cookie or JWT.
- `POST /api/analyze`: runs C4, saves via C6, links the uploader as a tenant, returns the case.
- `GET /api/cases` (my cases), `GET /api/cases/{code}` (only for linked users),
  `POST /api/cases/{code}/join`.
- `PATCH /api/flags/{id}` to set `status` open/resolved (P1).
- Placeholder mode: with no Gemini key or no DB, return fixed sample results so the demo
  never crashes.
- *(scaffold)* FastAPI, routes under `/api`; `backend/main.py`.

### C4. Analysis pipeline (P0, the core)
1. PDF → text (pypdf); split into numbered clauses, keeping the printed number (`clauses.number`).
2. Retrieve law: embed each clause, top-k `law_sections` from C5; merge and de-duplicate.
3. **One Gemini call** with the whole agreement + retrieved sections, structured JSON output:
   a `summary`, a separate `need_to_knows` list, and `flags` of kind `clarify` /
   `contradicts_law` with explanation and citation (section 7). Agreements over the size
   limit get a clear error rather than a split call.
4. **Guardrail**: the model may only cite sections it was given; a `contradicts_law` flag
   whose citation isn't in the retrieved set is downgraded to `clarify` with a note. Map
   citations to `law_section_id` and keep a text snapshot (`cited_source`, `cited_section`).
5. **One more call** drafts the landlord email from the `clarify` and `contradicts_law` flags,
   with the disclaimer.
- Implemented in PR #5 (`ui` branch, kept by Andrea): `backend/app/pipeline.py`,
  `backend/app/llm.py` (`AGREEMENT_SCHEMA`, `analyze_agreement`), with tests in
  `backend/tests/test_pipeline.py`.

### C5. Law knowledge base (P0)
- Sources: Housing Act 1988, Housing Act 2004, Tenant Fees Act 2019, Renters' Rights Act 2025,
  tenancy deposit protection rules, gov.uk "How to rent". Trim to the relevant parts (fees,
  deposits, notice, repairs, access, guests/subletting). Save as `backend/data/law/<name>.txt`.
- Chunk by section (`section_ref`, `heading`, `text`, deep-link `url`); embed at 768 dims;
  store once in `law_sources` / `law_sections` with each source's last-updated date.
  HNSW cosine index. Local numpy index as fallback when `DATABASE_URL` is unset.
- Ingest script re-runnable per source (delete source row, insert again).
- *(scaffold)* `backend/app/rag/*`, `backend/scripts/ingest_law.py`.

### C6. Case store (P0)
- Tables from DB draft v4 (section 6). Save each analysis as: case (code, address, postcode,
  dates) → tenants → agreement (text) → clauses → flags; email (latest draft only).
- Case code generator, short and readable (e.g. `FLAT-7KQ2`), unique.
- Re-uploading an agreement replaces the agreement, its clauses and flags.

### C7. Move-in photos (P1, stretch)
- Upload photos per room; file in Vultr Object Storage, row in `photos` (room, object_key,
  taken_at from EXIF, note). No AI on images.
- Gallery on the case page grouped by room with dates. Evidence for deposit disputes.

### C8. End-of-tenancy calendar (P2, stretch)
- From `tenancy_start` / a chosen notice date, compute the earliest end date (2 months'
  notice by default, or the period found in the agreement).
- Pull move-out need-to-knows from the saved clauses (cleaning, inventory, deposit return).
- Dated list of steps; `.ics` export is a nice-to-have.

### C9. Deployment + DevOps (P0)
- Vultr Cloud Compute VM (Ubuntu), HTTPS via Caddy or nginx, the app as a systemd service.
- Tiger Data service + `DATABASE_URL`; run the schema SQL; Vultr Object Storage bucket (if C7).
- `.env` on the server only. A one-command deploy script.
- Deploy a hello-world early (hour 2-4) so the final deploy isn't a surprise.

### C10. Demo assets + pitch (P0)
- 2-3 sample tenancy agreements: one clean, one with planted illegal fees / deposit issues,
  one with vague clauses. Expected flags written down for each (our test set).
- Two demo accounts (lead tenant, co-tenant) for the join-by-code moment.
- Slides, script, screen recording backup in case the live demo fails.
- Sponsor slide: what Gemini, Vultr and Tiger Data each do; incident.io as the roadmap.

---

## 6. Data model

Full SQL and reasoning: **db-schema-draft.md** (v4, Tiger Data database design thread;
in the project files, not in the repo yet). Summary:

```
law_sources    id, name, url, last_updated, ingested_at
law_sections   id, source_id → law_sources, section_ref, heading, text, url, embedding vector(768)
users          id, email, display_name, password_hash, google_id (future), created_at
cases          id, code (unique), property_address, postcode, tenancy_start, tenancy_end, created_at
tenants        id, case_id → cases, user_id → users (null until they join), name, role (lead | co_tenant)
agreements     id, case_id → cases (1:1), original_filename, object_key, page_count, text, uploaded_at
clauses        id, agreement_id → agreements, number, text
flags          id, clause_id → clauses, kind (need_to_know | clarify | contradicts_law), explanation,
               law_section_id → law_sections, cited_source, cited_section, status (open | resolved)
emails         id, case_id → cases (1:1, latest draft), body, created_at
photos         id, case_id → cases, room, object_key, taken_at, uploaded_at, note
```

Owner of schema changes: workstream B. Change the draft file first, then the SQL.

## 7. Contracts between components (agree these first)

Agreeing these in the first hour lets frontend, backend and pipeline work in parallel.

The response shape is the one in **PR #5** (`ui` branch), which Andrea decided to keep:
a `summary`, a separate `need_to_knows` list, and `flags`. Fields marked *(cases)* are added
when accounts and saved cases land (workstream B); everything else already exists in PR #5.

`POST /api/analyze` (multipart): `agreement` (PDF), `lead_tenant`, `co_tenants` (optional,
comma-separated), `property_address`. *(cases)* adds: logged-in session, `postcode`,
`tenancy_start`, `tenancy_end` (optional).

Response (and *(cases)* `GET /api/cases/{code}`):

```json
{
  "tenant_name": "...",
  "co_tenants": ["..."],
  "property_address": "...",
  "filename": "agreement.pdf",
  "pages": 12,
  "mode": "gemini | placeholder",
  "law_data_available": true,
  "summary": "Plain-English summary of the agreement.",
  "need_to_knows": [
    {"topic": "Guests", "clause_ref": "7.2", "text": "Guests may stay up to 3 nights a week."}
  ],
  "flags": [
    {
      "kind": "clarify | contradicts_law",
      "clause_ref": "4.1",
      "clause_excerpt": "...",
      "explanation": "...",
      "citation": {"source": "Tenant Fees Act 2019", "section": "s.1", "url": "..."}
    }
  ],
  "email": "Dear ...",
  "disclaimer": "This tool provides information, not legal advice.",

  "case_code": "FLAT-7KQ2",
  "postcode": "...",
  "tenancy_start": "2026-09-01",
  "tenancy_end": "2027-08-31",
  "tenants": [{"name": "...", "role": "lead", "joined": true}]
}
```

- The last block of fields is *(cases)*. With saved cases, each flag also gets `id` and
  `status` (`open | resolved`) so it can be ticked off.
- `citation` is `null` when a flag cites nothing (e.g. a `clarify` flag, or a downgraded one).
- The frontend shows `summary`, then `need_to_knows`, then flags split by `kind` into
  Questions to ask (`clarify`) and Possible legal issues (`contradicts_law`).
- Gemini's structured output (`AGREEMENT_SCHEMA` in `llm.py`) returns `summary`,
  `need_to_knows` and `flags` (with `citation_source` / `citation_section`); the backend
  resolves the citation against the retrieved sections after the guardrail.
- **Storage (for workstream B / schema v4):** `need_to_knows` can be saved as `flags` rows
  with `kind = need_to_know` and returned separately, or get their own table; `summary` needs
  a column (e.g. `agreements.summary`). Decide in the database design thread.

---

## 8. Work split

Five workstreams; one person can take two small ones. **Owners: TBD (fill in names).**

| Workstream | Components | Owner | First deliverable |
|---|---|---|---|
| A. Frontend | C1, C1b, C2, (C7 UI, C8 UI) | _TBD_ | Form + results page working against mock JSON |
| B. Backend + data | C3, C6, (C7 storage) | _TBD_ | Schema created in Tiger Data; `/api/analyze` returning placeholder JSON in the agreed shape |
| C. AI pipeline | C4 | _TBD_ | One Gemini call returning findings for one sample agreement |
| D. Law knowledge base | C5 | _TBD_ | Law texts ingested at 768 dims; retrieval returns sensible sections |
| E. Deploy + demo | C9, C10, (C8 logic) | _TBD_ | Hello-world on Vultr over HTTPS; sample agreements with expected flags |

Everyone: follow CLAUDE.md's git rule. Show the full diff in chat and get a teammate's OK
before committing or pushing; never commit `.env` or keys.

### Task list

P0 first, top to bottom.

**Hour 0-1: everyone**
- [ ] Confirm OPEN-1 (stack) and decide OPEN-2 (England only?), OPEN-3 (store PDF?)
- [ ] Start the design and branding thread (OPEN-4); name and icon wanted by hour 6
- [ ] Agree the API contract (section 7); schema v4 is the starting point
- [ ] Assign owners to workstreams A-E
- [ ] Everyone runs the repo locally; Gemini key and Tiger Data credentials shared privately

**A. Frontend**
- [ ] A1 Agreement form: tenants, address, postcode, dates, PDF upload, validation (P0)
- [ ] A2 Loading + error states (P0)
- [ ] A3 Results: need-to-knows, Questions-to-ask and Legal-issue cards with clause text + citation (P0)
- [ ] A4 Landlord email box + Copy email button (P0)
- [ ] A5 Sign up / log in / log out pages (P0)
- [ ] A6 My cases list + case page + Join a case by code (P0)
- [ ] A7 Disclaimer, header, mobile-first styling with placeholder theme tokens (P0)
- [ ] A7a Apply the agreed branding: name, logo, icons, colours, fonts (P0, after OPEN-4)
- [ ] A7b PWA: manifest, icons, service worker; test add-to-home-screen on Android and iPhone (P0)
- [ ] A8 Mark a flag resolved (P1)
- [ ] A9 Photo upload from the phone camera + gallery by room (P1, if C7)
- [ ] A10 End-of-tenancy calculator UI (P2)

**B. Backend + data**
- [ ] B1 `/api/analyze` returns placeholder JSON in the agreed shape (P0, unblocks A and E)
- [ ] B2 Tiger Data service, connection, run schema v4 SQL (P0)
- [ ] B3 Auth: signup/login/logout/me with hashed passwords and sessions (P0)
- [ ] B4 Save case + tenants + agreement + clauses + flags + email; case code generator (P0)
- [ ] B5 `GET /api/cases`, `GET /api/cases/{code}` (linked users only), `POST /api/cases/{code}/join` (P0)
- [ ] B6 Wire C4 into `/api/analyze`; placeholder fallback when keys/DB missing (P0)
- [ ] B7 `PATCH /api/flags/{id}` status (P1)
- [ ] B8 Photo upload → Vultr Object Storage + `photos` row (P1)
- [ ] B9 End-of-tenancy date endpoint (P2)

**C. AI pipeline**
- [ ] C1 PDF → text → numbered clauses; test on the sample agreements (P0)
- [ ] C2 Prompt + JSON schema for the single analysis call (P0)
- [ ] C3 Add retrieved law from D to the prompt; citation guardrail; map to `law_section_id` (P0)
- [ ] C4 Landlord email call with disclaimer (P0)
- [ ] C5 Compare against E's expected flags; tune the prompt (P0)
- [ ] C6 Very long agreements (split) and Gemini errors / rate limits (P1)

**D. Law knowledge base**
- [ ] D1 Collect and trim the six law sources into text files with section headings (P0)
- [ ] D2 Switch embeddings to 768 dims; chunk, embed, store once in `law_sources` / `law_sections` with last-updated dates; local fallback (P0)
- [ ] D3 Retrieval: clause in → top-k sections out; sanity-check results (P0)

**E. Deploy + demo**
- [ ] E1 Vultr VM + HTTPS + hello-world deployed (P0, early)
- [ ] E2 Deploy script; `.env` on server; Tiger Data + Object Storage credentials (P0)
- [ ] E3 Sample agreements with planted issues + expected flags; two demo accounts (P0, unblocks C5)
- [ ] E4 Full deploy of the main flow; smoke test on the live URL (P0)
- [ ] E5 Pitch slides, demo script, backup recording (P0)
- [ ] E6 Notice-period / end-of-tenancy logic (P2)

## 9. Timeline (24h)

| Hours | Goal | Checkpoint |
|---|---|---|
| 0-1 | Decisions, contracts, owners | Section 3 decided; section 7 agreed |
| 1-6 | Build in parallel against placeholders | Frontend shows mock results; schema live in Tiger Data; law ingested; hello-world on Vultr |
| 6-12 | Connect the real pipeline + auth | Log in → upload a sample PDF → real Gemini flags with citations → saved case |
| 12-16 | **Main flow working end to end on Vultr** | Demo story steps 1-6 work on the live URL, on a laptop and a phone |
| 16-20 | Stretch: resolve flags, photos, then calendar | One extra demoable |
| 20-23 | Freeze features, polish, fix bugs, rehearse | Backup recording made |
| 23-24 | Submit + pitch | |

Rule: if the main flow isn't working on Vultr by hour 16, all stretch work stops.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Gemini rate limits / errors mid-demo | One analysis call + one email call; cache the demo sample's result; placeholder fallback |
| Model claims something is illegal without basis | Only cite retrieved sections; drop unsupported `contradicts_law`; disclaimer |
| Auth eats build time | Plain email + password with a well-known library; no email verification or password reset in the MVP |
| PDF text extraction fails (scanned PDF) | Use text-based sample PDFs for the demo; show a clear error otherwise |
| Embedding size mismatch (3072 vs 768) | D2 switches to 768 before anything is ingested into Tiger Data |
| Gemini account region issue (HK Gmail) | Create the API key from an account that works; calls come from the Vultr server |
| Live deploy breaks at the end | Deploy early (E1), deploy often, keep a backup recording |
| Secrets leak | Keys only in `.env` and shared privately; never committed or posted |

## 11. After the hackathon (not in scope)

**Legislation-change scan with incident.io** (parked by Andrea, 2026-10-17). Only for new or
changed legislation, with a human in the loop: a daily job compares each law's last-updated date
on legislation.gov.uk with the date in Tiger Data; on a mismatch it raises an incident in
incident.io; a person approves before that law is re-stored. Users keep using the app on the
current law meanwhile.

**Google sign-in** (Andrea, 2026-10-17). The MVP uses email + password; Google sign-in comes
later. Suggested service: Firebase Authentication (free at our scale, works on web and the
phone app; FastAPI only verifies its ID token). Alternatives: Auth0 or Clerk, or Authlib in
FastAPI with no third party. The `users` table already has a nullable `google_id` for it.

Other ideas from CLAUDE.md's Later list and the Google Doc, roughly in priority order:
end-of-tenancy calendar with bill and meter-reading reminders (if not built as a stretch),
next-steps advisor (council tax, bills setup), bills estimate by region (EPC, energy prices,
permits), move-in photos (if not built as a stretch), landlord reply interpreter, tenancy
buddy chatbot, user modes (single / family / friends, HMO), native mobile app (if the PWA isn't enough, e.g. push reminders on iPhone), property-viewing tracker add-on, landlord reviews, docx/txt uploads, email
provider integration.
