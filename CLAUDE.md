# Tenancy Demystifier (IKU Hackathon 2026)

## Rules for Claude (read first)

1. **Never `git push` (including creating remote branches) without the user's explicit
   confirmation for that specific push.** Commit locally, then ask.
2. Work on feature branches off `main` (`feature/<name>-<thing>`). Never commit to `main`
   directly.
3. Never read, print or commit secrets (`.env`, API keys). `.env.example` holds empty
   placeholders only.
4. Stick to the MVP scope below. Ask before adding features, new dependencies or new services.
5. Before saying something works, actually run it: backend starts, `/api/health` responds,
   `npm run build` passes, plus whatever the change touches.
6. Report every change as a precise list: files added/changed (one line each on what and
   why), what you tested and the result, and what you did *not* test.
7. Follow `.claude/skills/feature-workflow/SKILL.md` for any code change.

---

24-hour hackathon project, **Society Track** (a real problem people face during a transition).
Ours: students and people new to UK renting sign long tenancy agreements they don't fully
understand, and miss clauses that cause legal or communication problems later.

Source of truth for the idea: Google Doc "IKU Hackathon"
(https://docs.google.com/document/d/1X7dr80E1U_18iKHcWF1CMMxvI_mh_Pf1YdD6vRWGqSc/edit).
It is a live team doc; if it disagrees with this file, the doc wins and this file should be updated.

## Hackathon priorities

1. A working demo of the main flow below, end to end, deployed on Vultr.
2. Sponsor integrations that strengthen the demo (Gemini, Vultr, Tiger Data, incident.io).
3. Nothing else until 1 and 2 work. Prefer the simplest thing that demos well: hard-code,
   stub, or use placeholder data rather than build features from the "Later" list.

The app must always demo without crashing: missing keys or empty data fall back to
placeholder output (see `mode: "placeholder"` below).

## MVP: main flow (UI wireframe)

1. Enter tenant and property details: lead tenant / co-tenant name(s), property address.
2. Upload the tenancy agreement (PDF).
3. Start the analysis.
4. Review the results:
   - **Need-to-knows**: key clauses people miss (guest policy, what is / isn't allowed,
     included services and bills, charges).
   - **Flags**, two kinds:
     - `clarify`: needs clarification / confirmation from the landlord.
     - `contradicts_law`: conflicts with UK law (Housing Act, Tenant Fees Act, Renters'
       Rights Act, deposit rules), citing the retrieved section.
5. Copy the draft email to the landlord raising the flags (copy/paste only; no Gmail /
   Outlook integration in the MVP).

Results are saved so later features can reuse them throughout the tenancy. The address is
kept for the bills and area features later.

## Later (not MVP, in rough priority order)

- **End-of-tenancy calendar**: if the tenant chooses to leave, calculate the date from the
  2 months' notice period, then re-read the agreement for the legal steps and need-to-knows
  for moving out. The same calendar holds bill-payment and meter-reading reminders.
- **Next-steps advisor**: council tax, bills to set up (energy, WiFi, parking permits) and
  how to reduce them, based on the address.
- **Bills estimate by region**: EPC rating, council tax, average energy prices (track rises
  and notify), parking permits; toggle off what the agreement already includes.
- **Move-in photos**: upload photos at move-in and store them in the database as dated
  evidence, in case a landlord raises an issue later (e.g. deposit deductions). Photos are
  stored only; no LLM analysis of images.
- **Landlord replies**: upload or read landlord emails and interpret them.
- **Tenancy buddy chatbot**: help during the tenancy; contact the landlord about issues.
- **User modes**: single / family / friends (tenancy swaps), HMO or not; changes what is
  flagged as important.
- **Tenancy code + mobile app**: a code per tenancy shared by co-tenants (lead vs co-tenant,
  who holds the energy account) that unlocks the app.
- **Property-viewing tracker add-on** (before signing): spreadsheet of viewed properties,
  likes/dislikes, group preferences, and area filters for structural history (mould,
  floods, gas leaks, electric vs gas heating, maintenance), EPC checker.
- **Legislation watch (incident.io)**: a daily job compares each law source's
  last-updated date with the one stored in Tiger Data. If it changed, it raises an
  incident in incident.io; a person reviews it before the law is rescanned and stored.
  Only for new legislation, always with a human in the loop.
- Future: landlord reviews, verified government-sourced dataset, paywall on the chatbot,
  solicitor partnership, email provider integration.

## Sponsor tracks / services

- **Gemini API**: text only (not used on images). Two jobs:
  - Embeddings for the law sections and agreement clauses.
  - Analysis: the whole agreement plus the retrieved law sections go in one call
    (batched only for very long agreements), then one call drafts the landlord email.
    Never one call per clause, to stay inside the free tier's per-minute limits.
  Default model `gemini-flash-latest` (`GEMINI_MODEL` in `.env`). The Gemini Pro student
  offer is for the Gemini app, not the API; the API runs on an AI Studio key.
- **Vultr**:
  - Cloud Compute VM running the app (frontend + backend, one process).
  - Object Storage (S3-compatible) for move-in photo files.
- **Tiger Data** (Postgres + pgvector):
  - Law knowledge base: each source's sections and embeddings, stored once, with the
    source's last-updated date.
  - User data: tenancies (case number, tenants, address), extracted clauses and flags,
    and photo records (link to the file in Vultr, date, room). Schema: `backend/db/schema.sql`.
- **incident.io**: future extension, not in the MVP (see Later).

## Stack

- `index.html`: standalone upload/results page served by FastAPI when present.
- `frontend/`: React 19 + Vite + TypeScript alternative frontend (`src/App.tsx`).
- `backend/`: FastAPI (Python). Routes live under `/api`: `GET /api/health`,
  `POST /api/analyze`. Config in `app/config.py`, read from `backend/.env` (copy
  `backend/.env.example`).
- The analysis form accepts the PDF, `lead_tenant`, optional comma-separated `co_tenants`,
  and `property_address`. The response includes a summary, need-to-knows, flags,
  citations, email, analysis mode, and whether relevant law sections were found. The older
  `tenant_name` form field remains accepted for compatibility.
- FastAPI serves the standalone `index.html` at `/` when it exists; otherwise it serves the
  built React frontend (`frontend/dist`). The app is one process on one URL. In dev, Vite
  proxies `/api` to FastAPI on port 8000.

## Analysis pipeline (RAG over UK housing law)

`POST /api/analyze` (`backend/app/pipeline.py`):

1. PDF → text (pypdf); reject scanned PDFs with no selectable text and documents over the
   100,000-character analysis limit.
2. Split into clauses and batch their embeddings to retrieve relevant law sections
   (`app/rag/retrieve.py`).
3. Gemini makes one structured call for the agreement summary, need-to-knows, and flags
   (`app/llm.py`). Legal flags are only retained when their citation matches a retrieved
   source and section.
4. Draft the landlord email from the verified or clarification flags.

Without `GEMINI_API_KEY` the endpoint returns an explicit unanalyzed placeholder response
(`mode: "placeholder"`). Law source files are not included; add them under
`backend/data/law/` before expecting law-grounded results. Re-running ingestion replaces the
chunks for each supplied source rather than appending duplicates.

Knowledge base:
- Sources (list in `scripts/ingest_law.py`): Housing Act 1988, Housing Act 2004, Tenant
  Fees Act 2019, Renters' Rights Act 2025, tenancy deposit protection, gov.uk "How to rent".
  Save each as text in `backend/data/law/<name>.txt`, then from `backend/` run
  `.venv\Scripts\python -m scripts.ingest_law`.
- Chunked by section (`app/rag/ingest.py`), embedded with Gemini embeddings.
- Stored in Tiger Data when `DATABASE_URL` is set; otherwise a local numpy index at
  `backend/data/law_index.npz` (git-ignored). Both implement `VectorStore` (`app/rag/store.py`).
- The LLM must only cite sections it was given; the curated law text is the guardrail.
  The app gives information, not legal advice; say so in the UI and the email.

## Run locally (Windows PowerShell)

Backend:

    cd backend
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\uvicorn main:app --reload --port 8000

Standalone app (after starting the backend):

    # Open http://localhost:8000

Alternative React frontend (second terminal):

    cd frontend
    npm install
    npm run dev          # http://localhost:5173

Production-style (one process): run uvicorn as above and open http://localhost:8000. To use the
React frontend instead, build it with `cd frontend; npm run build` and remove or rename the
standalone `index.html` so FastAPI serves `frontend/dist`.

## Deploy on Vultr

One Ubuntu Cloud Compute VM: build the frontend, run `uvicorn main:app` from `backend/` as
a systemd service, with Caddy (or nginx) in front for HTTPS. Keep `.env` on the server only.
Tiger Data stays a separate hosted database.

## Environment and conventions

- Windows 11 laptop: Node 24, npm 11, Python 3.14, Git, GitHub CLI (`gh`, signed in as
  `awckiu`). Give commands for PowerShell.
- Secrets (Gemini, Vultr, Tiger Data) go in `.env` files, which are git-ignored. Never
  commit them.
- **Ask before pushing.** Never push or open a PR until a team member has approved it.
  Before asking, show every change in the chat in full: the complete diff, or the whole
  file for new files. A summary or an attachment alone is not enough.
- Never commit secrets (API keys, `.env` files), and never paste them into diffs shown
  in chat.
- Keep changes small and demoable; commit working states often.
