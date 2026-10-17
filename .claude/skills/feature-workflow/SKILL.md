---
name: feature-workflow
description: Workflow for any code change in Tenancy Demystifier - branch off dev, implement within MVP scope, test, report precisely, and only git push after explicit user confirmation. Use before starting any feature, fix or refactor in this repo.
---

# Feature workflow

Follow these steps for every change. Don't skip the test or the ask.

## 1. Branch

```
git switch dev
git pull
git switch -c feature/<name>-<thing>
```

Never commit directly on `main` or `dev`.

## 2. Check scope

- Is it in the MVP scope in `CLAUDE.md`? If not, ask the user first.
- New dependency or external service? Ask first, and say why it's needed.
- Never read or print `backend/.env`. Need a new secret? Add an empty placeholder to
  `backend/.env.example` and tell the user to fill in `.env` themselves.

## 3. Implement

Keep changes small and focused, and match the existing code style.

## 4. Test (all that apply)

- Backend: `backend\.venv\Scripts\python -m uvicorn main:app --port 8000` starts, and
  `GET /api/health` returns `status: ok`.
- Changed an endpoint: call it (e.g. `curl.exe -F ...` for `/api/analyze`) and check the response.
- Frontend: `npm run build` in `frontend/` passes (this includes the TypeScript check), and
  `npm run dev` serves the page.
- Stop any servers you started.

## 5. Commit locally

Use a clear message saying what changed and why. Check `git status` before committing so
no `.env`, `.venv`, `node_modules` or build output is staged.

## 6. Report, then ask

Tell the user:
- **Files changed**: each path, with one line on what and why
- **Tested**: commands run and their results
- **Not tested**: e.g. anything needing keys you don't have
- **Proposed push**: the exact commands, e.g.
  `git push -u origin feature/<name>-<thing>` and `gh pr create --base dev`

Then **stop and wait for explicit confirmation** before any `git push`.
