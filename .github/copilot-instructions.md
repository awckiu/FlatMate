# Copilot instructions: Tenancy Demystifier (IKU Hackathon 2026)

GitHub Copilot reads this file automatically. If a teammate asks you to "set up the
project", "get it running" or similar, walk them through **Setup** below one step at a time:
run or show the command, check the result, and only then move on. The full project notes
are in `CLAUDE.md` and `README.md`; if they disagree with this file, they win.

## Rules (always follow)

1. **Never read, print, paste or commit secrets.** `backend/.env` holds the Gemini key and
   the Tiger Data connection strings. Don't open it, `cat` it, or echo its values. The
   teammate types their own keys into it. `backend/.env.example` holds empty placeholders only.
2. **Never `git push`, and never open a PR, without the teammate's explicit OK** for that
   push. Commit locally first, show the diff, then ask.
3. Work on a feature branch off `main` (`feature/<name>-<thing>`). Never commit to `main`.
   PRs target `main`.
4. **Don't change the databases** while setting up: no `CREATE`, `ALTER`, `DROP`, no running
   `backend/db/schema.sql`, and no running `scripts.ingest_law` against `DATABASE_URL`. The
   main database is already set up and shared by the whole team.
5. Stick to the MVP scope in `CLAUDE.md`. Ask before adding dependencies or services.
6. The teammate may be on Windows (PowerShell) or macOS/Linux. Commands below are PowerShell;
   on macOS/Linux use `.venv/bin/...` instead of `.venv\Scripts\...` and `cp` instead of `copy`.

## Setup

### 1. Check the tools

```powershell
git --version      # any recent version
python --version   # 3.11 or newer
node --version     # 20 or newer
npm --version
```

If one is missing, point the teammate to the installer (Windows: `winget install Git.Git`,
`winget install Python.Python.3.13`, `winget install OpenJS.NodeJS.LTS`) and stop until it
works. GitHub CLI (`winget install GitHub.cli`, then `gh auth login`) is optional but helps
with PRs.

### 2. Get the code

```powershell
git clone https://github.com/awckiu/tenancy-demystifier.git
cd tenancy-demystifier
git switch main
git pull
```

If they already have a clone, just run `git switch main` and `git pull` inside it.

### 3. Backend: Python environment

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Check: the install ends without errors. If PowerShell blocks scripts, the commands above
still work because they call `.venv\Scripts\...` directly (no need to "activate").

### 4. Backend: secrets file

```powershell
copy .env.example .env
```

Then tell the teammate to open `backend/.env` **themselves** and fill in:

| Variable | Where it comes from |
|---|---|
| `GEMINI_API_KEY` | Their own free key from Google AI Studio (https://aistudio.google.com/apikey) |
| `DATABASE_URL` | The team's Tiger Data connection string. Andrea shares it privately (never in GitHub, issues or PRs) |
| `TEST_DATABASE_URL` | Optional. The separate test database, also shared privately by Andrea. Use it for anything that writes to a database |

Leave `GEMINI_MODEL` and `GEMINI_EMBED_MODEL` as they are. Both keys are optional for a first
run: without `GEMINI_API_KEY` the app returns placeholder results (`mode: "placeholder"`),
and without `DATABASE_URL` it uses a local index instead of Tiger Data.

Do not open or print `.env` to check it. Step 5 checks it safely.

### 5. Check the database connection (read-only)

Only if `DATABASE_URL` is set. From `backend/`:

```powershell
@'
import psycopg
from app import config
print("DATABASE_URL set:", bool(config.DATABASE_URL))
with psycopg.connect(config.DATABASE_URL) as c:
    print("Connected:", c.execute("select current_database(), count(*) from information_schema.tables where table_schema = 'public'").fetchone())
    print("vector:", c.execute("select extversion from pg_extension where extname = 'vector'").fetchone())
'@ | .venv\Scripts\python -
```

Expected output:

```
DATABASE_URL set: True
Connected: ('tsdb', 12)
vector: ('0.8.7',)
```

If it fails, report the error with any password or host removed. Common causes: the URL
was pasted with spaces or quotes around it, it's missing `?sslmode=require` at the end, or a
special character in the password needs URL-encoding (for example `@` as `%40`).

To check the test database instead, replace `config.DATABASE_URL` with
`os.getenv("TEST_DATABASE_URL")` and add `import os` at the top. It's a separate, mostly
empty service, so expect `('tsdb', 2)` and `vector: None` until the team sets it up.

### 6. Start the backend

From `backend/`:

```powershell
.venv\Scripts\uvicorn main:app --reload --port 8000
```

In a second terminal, check it:

```powershell
curl.exe http://localhost:8000/api/health
```

Expected: `"status":"ok"`, plus `"gemini_configured":true` if the Gemini key is set and
`"vector_store":"tiger_data"` if `DATABASE_URL` is set (`"local"` otherwise).

Then open http://localhost:8000 in a browser. That serves the standalone `index.html` page,
where you can upload a tenancy agreement PDF.

### 7. (Optional) React frontend

Only needed when working on `frontend/`. Keep the backend running, and in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite forwards `/api` calls to the backend on port 8000. Before
committing frontend changes, `npm run build` must pass.

### 8. Start working

```powershell
git switch main
git pull
git switch -c feature/<yourname>-<thing>
```

Commit often with clear messages. Before committing, check `git status` so no `.env`,
`.venv`, `node_modules` or `dist` files are staged. When ready, show the teammate the diff
and ask before running `git push -u origin feature/<yourname>-<thing>` and
`gh pr create --base main`.

## Project in one paragraph

Upload a UK tenancy agreement (PDF) plus tenant names and address. The FastAPI backend
(`backend/`, routes under `/api`) extracts the text, retrieves relevant UK housing law
sections (RAG, Gemini embeddings, Tiger Data Postgres + pgvector), makes **one** Gemini call
for the summary, need-to-knows and flags (`clarify` / `contradicts_law`), and drafts an email
to the landlord. Never make one Gemini call per clause (free-tier rate limits). The app gives
information, not legal advice. The app must always demo without crashing: missing keys fall
back to placeholder output.
