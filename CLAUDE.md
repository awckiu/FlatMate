# Tenancy Demystifier (IKU Hackathon 2026)

## Rules for Claude (read first)

1. **Never `git push` (including creating remote branches) without the user's explicit
   confirmation for that specific push.** Commit locally, then ask.
2. Work on feature branches off `dev` (`feature/<name>-<thing>`). Never commit to `main` or
   `dev` directly.
3. Never read, print or commit secrets (`.env`, API keys). `.env.example` holds empty
   placeholders only.
4. Stick to the MVP scope below. Ask before adding features, new dependencies or new services.
5. Before saying something works, actually run it: backend starts, `/api/health` responds,
   `npm run build` passes, plus whatever the change touches.
6. Report every change as a precise list: files added/changed (one line each on what and
   why), what you tested and the result, and what you did *not* test.
7. Follow `.claude/skills/feature-workflow/SKILL.md` for any code change.

---

Source of truth for the idea: Google Doc "IKU Hackathon"
(https://docs.google.com/document/d/1X7dr80E1U_18iKHcWF1CMMxvI_mh_Pf1YdD6vRWGqSc/edit).

## Goal

IKU Hackathon, **Society Track**: tackle a real problem people face during a transition
(starting uni, entering tech, ...). Our transition: students and people new to UK renting
who sign tenancy agreements they don't fully understand.

## MVP scope

1. **Inputs**: upload tenancy agreement (PDF), lead tenant / co-tenant name(s), property address.
2. **Agreement analysis (Gemini API)**: extract key clauses and the need-to-knows people miss
   (guest policy, what is / isn't allowed, included services, illegal charges). Save the
   results so they can be used throughout the tenancy.
3. **Flags**, two kinds:
   - clarification / confirmation needed from the landlord
   - contradictions with UK law (Housing Act, Renters' Rights). Backed by a curated
     dataset of UK legislation used as guardrails for the LLM.
4. **Landlord email**: generate an email raising the flagged issues (copy/paste only;
   no Gmail/Outlook integration in the MVP).

## Later (not MVP)

- Interpret replies/emails from the landlord.
- User modes: single / family / friends, HMO.
- Bills estimate by region (EPC, council tax, energy prices, parking permits) + reminders.
- Tenancy chatbot "buddy".
- Tenancy code shared between co-tenants that unlocks a mobile app.
- Move-in photo inspection (mould, damage, ...).
- Future: landlord reviews, verified government dataset, paywall, rental tracker add-on.

## Planned services / sponsor tracks

- **Gemini API**: LLM for agreement extraction and flagging.
- **Vultr**: deploy frontend + backend.
- **Tiger Data**: store address/area data and common property issues.
- **incident.io**: possible adaptive AI agents for changing legislation.

## Stack

- `frontend/`: React + Vite + TypeScript. Upload page (PDF, tenant name, address).
- `backend/`: FastAPI (Python). Routes live under `/api` (`/api/health`, `POST /api/analyze`,
  which is a stub for now). Reads `GEMINI_API_KEY` from `backend/.env`. Copy
  `backend/.env.example` to create it.
- In production, FastAPI serves the built frontend (`frontend/dist`), so the app runs as one
  process on one URL. In dev, Vite proxies `/api` to FastAPI on port 8000.

## Target architecture (RAG over UK housing law)

`POST /api/analyze` pipeline (`backend/app/pipeline.py`):

1. PDF → text (pypdf) → split into numbered clauses.
2. For each clause, retrieve the top-k relevant law sections (`app/rag/retrieve.py`).
3. Gemini with structured JSON output classifies the clause as `ok` / `clarify` /
   `contradicts_law`, citing a retrieved section (`app/llm.py`).
4. Draft the landlord email from the flags.

Without `GEMINI_API_KEY` the endpoint returns placeholder flags (`mode: "placeholder"`).

Knowledge base:
- Sources: Housing Act 1988, Housing Act 2004, Tenant Fees Act 2019, Renters' Rights Act 2025,
  tenancy deposit protection rules, gov.uk "How to rent". Save each as text in
  `backend/data/law/<name>.txt` (names are in `scripts/ingest_law.py`), then run
  `.venv\Scripts\python -m scripts.ingest_law` from `backend/`.
- Chunked by section (`app/rag/ingest.py`), embedded with Gemini embeddings.
- Stored in Postgres + pgvector on **Tiger Data** when `DATABASE_URL` is set; otherwise a
  local numpy index at `backend/data/law_index.npz` (git-ignored). Both implement the
  same `VectorStore` interface (`app/rag/store.py`).

Later: an incident.io-style agent that watches for legislation changes and re-ingests,
Tiger Data area/property-issue data, bills estimator, mobile app.

## Run locally (Windows PowerShell)

Backend:

    cd backend
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\uvicorn main:app --reload --port 8000

Frontend (second terminal):

    cd frontend
    npm install
    npm run dev          # http://localhost:5173

Production-style (one process): `cd frontend; npm run build`, then run uvicorn as above
and open http://localhost:8000.

## Deploy on Vultr

One Ubuntu Cloud Compute VM. Build the frontend, run `uvicorn main:app` from `backend/`
(systemd service), with nginx or Caddy in front for HTTPS. Keep `.env` on the server
only. Tiger Data stays a separate hosted database. Details TBD.

## Environment

- Windows 11 laptop. Node 24, npm 11, Python 3.14, Git, and the GitHub CLI (`gh`, signed in
  as `awckiu`) are installed.
- Secrets (Gemini API key, Vultr key, Tiger Data credentials) go in `.env` files, which
  are git-ignored. Never commit them.
