# Tenancy Demystifier

Built for the **IKU Hackathon 2026 (Society Track)**.

> [!IMPORTANT]
> **Add your Gemini API key before running the backend.** Copy `backend/.env.example` to
> `backend/.env` and set `GEMINI_API_KEY=...` in it. **Never commit `.env`**: this repo is public.

Renting in the UK for the first time is confusing. Tenancy agreements are long, and it's
easy to miss clauses that matter or that are actually against the law. Tenancy Demystifier
lets you upload your tenancy agreement and then:

- pulls out the key clauses people usually miss (guests, what's allowed, included services, fees)
- flags anything that needs **clarifying** with the landlord
- flags anything that may **contradict UK law** (Housing Acts, Tenant Fees Act, Renters' Rights Act), citing the law
- drafts a polite email to your landlord about the issues

The law checks use **RAG** (retrieval-augmented generation): the app first looks up the
relevant sections of UK housing law, then asks Gemini to judge each clause *only* against
those sections. That keeps its answers grounded in real law instead of guesses.

## Tech stack

| Part | Tech |
|---|---|
| Frontend | React + Vite + TypeScript (`frontend/`) |
| Backend | Python FastAPI (`backend/`) |
| AI | Google Gemini API (analysis + embeddings) |
| Law database | Tiger Data (Postgres + pgvector); a local file is used until it's set up |
| Hosting | Vultr (one Ubuntu server) |

## Run it on your laptop

You need **Node.js 20+**, **Python 3.11+** and **Git**.

```powershell
git clone https://github.com/awckiu/tenancy-demystifier.git
cd tenancy-demystifier
```

**1. Backend** (terminal 1):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then open .env and paste your GEMINI_API_KEY
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Run the command from the `backend` directory so Python uses the backend virtual environment
and can find `main.py`. On macOS/Linux use `.venv/bin/python -m uvicorn ...` and `cp` instead
of `copy`.

**2. Frontend** (terminal 2):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. If no Gemini key is set yet, the page shows a yellow
"Gemini API key missing" banner, and the app returns example flags instead of a real analysis.

**Never commit `backend/.env`.** It holds secret keys, and this repo is public. `.gitignore`
already excludes it, so don't force-add it.

## Rent and bills calendar

The signed-in tenancy workspace includes a calendar for one-off or monthly rent and bill due
dates. Email reminders are optional and are sent to the signed-in account email address two
UK calendar days before a due date. The backend must remain running for reminders to be checked
hourly.

To enable reminders, configure `REMINDER_SMTP_HOST`, `REMINDER_SMTP_PORT`, and
`REMINDER_FROM_EMAIL` in `backend/.env`. If your SMTP server requires authentication, also set
both `REMINDER_SMTP_USERNAME` and `REMINDER_SMTP_PASSWORD`; reminders remain unavailable until
the SMTP configuration is complete. `REMINDER_SMTP_USE_SSL=true` enables implicit TLS; otherwise
the server connection uses STARTTLS. Leave these settings blank to run the app without email
reminders. No SMTP credentials are needed for calendar entries themselves.

## Accounts and an ongoing tenancy

The standalone app served by FastAPI supports account registration, individual sign-in, and a
shared tenancy profile. During setup, choose whether you rent alone or share, enter the
agreement's tenant names and property details, and set the signing and tenancy-start dates. A
shared `HOME-...` code is issued only after the agreement is signed and the tenancy start date
has arrived. Co-tenants can use an account and join with that code; the name and role are
checked against the profile's tenant list, but this is not identity verification. An account
can have multiple tenancy records, with only one marked current.

Each saved agreement review also has a separate `FLAT-...` review code. Use its link or enter
the code on the start page to reopen the report; anyone with that code can view the saved
agreement review, so share it only with people you trust. This code does not unlock a tenancy
profile. The `HOME-...` tenancy code is issued later and is required to join the shared
ongoing-tenancy workspace.

After signing, the tenancy buddy can use saved agreement details to prepare general responses
and optional landlord email/text drafts. FlatMate does **not** send messages. Move-in photos
unlock when the tenancy starts; supported image uploads are stored as evidence and are not
analysed by AI.

For local development, photos are stored under `backend/data/photos`. To use private
S3-compatible storage, set `PHOTO_STORAGE_ENDPOINT`, `PHOTO_STORAGE_BUCKET`,
`PHOTO_STORAGE_ACCESS_KEY`, and `PHOTO_STORAGE_SECRET_KEY` in `backend/.env`; optionally set
`PHOTO_STORAGE_REGION`. Keep the bucket private. Behind HTTPS in production, set
`SESSION_COOKIE_SECURE=true` so the account session cookie is sent only over HTTPS.

Household and HMO selections provide context to legal retrieval, but do not themselves add
legislation to the law database or guarantee that a legal check is available. Populate the
law corpus before relying on legal flags. The buddy and agreement analysis are informational,
not legal advice.

## Deploy (Vultr), draft

> [!NOTE]
> Draft: we haven't deployed yet, so these steps will change.

The plan is one server that runs everything:

1. Create an **Ubuntu Cloud Compute** instance on Vultr.
2. Install Python, Node and Caddy (or nginx) on it, then clone this repo.
3. Build the frontend (`cd frontend && npm install && npm run build`). FastAPI serves the
   built files itself, so the whole app is one program on one web address.
4. Create `backend/.env` on the server with the real keys.
5. Run `uvicorn main:app --port 8000` as a background service (systemd), with Caddy in front
   for HTTPS.

Exact deploy scripts are still to be written.

---

## Working together: git housekeeping

We use three kinds of branches:

| Branch | What it's for | Who pushes to it |
|---|---|---|
| `main` | Demo-ready version we deploy | **Nobody directly.** `dev` is merged in only when the team agrees |
| `dev` | Where finished features come together | **Nobody directly.** Only via Pull Requests |
| `feature/<name>-<thing>` | Your own work in progress, e.g. `feature/andrea-upload-page` | You |

### Rules

1. **Never push directly to `main` or `dev`.** Create a feature branch from `dev`, push it, and open a Pull Request (PR) into `dev`.
2. **Pull before you start** so you're working on the latest code.
3. **Small, frequent commits** with clear messages, e.g. "Add address field to upload form".
4. **Never commit secrets** (`.env` files, API keys). If you do by accident, tell the group straight away so the key can be replaced.
5. Get **one other person to look at your PR** before merging it into `dev`.

### First-time git guide

If you've never used git before, this is all you need.

**One-time setup:**

1. Install [Git](https://git-scm.com/downloads) and the [GitHub CLI](https://cli.github.com/)
   (on Windows: `winget install Git.Git` and `winget install GitHub.cli`).
2. In a new terminal:

```powershell
gh auth login                        # choose GitHub.com, HTTPS, login with browser
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git clone https://github.com/awckiu/tenancy-demystifier.git
cd tenancy-demystifier
```

**Every time you start a new piece of work:**

```powershell
git switch dev                              # go to the dev branch
git pull                                    # get everyone's latest changes
git switch -c feature/yourname-upload-page  # make your own branch
```

**While you work**, save your progress:

```powershell
git status                           # see what you changed
git add .                            # stage your changes
git commit -m "Describe what you did"
```

**When you're ready to share:**

```powershell
git push -u origin feature/yourname-upload-page
gh pr create --base dev                     # opens a Pull Request into dev
```

(Or on GitHub click **"Compare & pull request"** and set the **base branch to `dev`**, not
`main`.) Then ask someone in the group to review it.

**To get other people's latest changes** into your branch: `git switch dev`, `git pull`,
`git switch feature/yourname-upload-page`, `git merge dev`.

**If git says there's a conflict:** don't panic and don't delete anything. Ask in the group
chat, or ask Claude for help.

**Useful words:**
- *commit*: a saved snapshot of your changes
- *branch*: your own copy of the code to work on without affecting others
- *push*: upload your commits to GitHub
- *pull*: download other people's commits
- *pull request (PR)*: asking for your branch to be merged into `dev`
