# Parker Review Prep — 2026-06-03

## Repo state

- Repo: `/Users/prasithgovin/Development/personal/parkinsons-assistant`
- Remote: `https://github.com/prasithg/parker.git`
- Branch: `hermes/capture-resolve-resurface-v0`
- Status before this note: branch is ahead of `origin/main` by 1 commit; uncommitted changes in `.gitignore`, `Makefile`, and `README.md`.
- Local review artifact added: `REVIEW-PREP-2026-06-03.md`.

## Feature commit under review

Commit: `3011053 feat: add Parker capture resolve resurface v0`

Files changed in commit:

- `backend/app/conversation/tools.py`
- `backend/app/db/models.py`
- `backend/app/main.py`
- `backend/app/parker/__init__.py`
- `backend/app/parker/pipeline.py`
- `backend/app/parker/router.py`
- `backend/tests/test_parker.py`

Feature summary:

- Adds a Parker capture → resolve → stage → resurface vertical slice.
- Adds `capture_intent` as an OpenAI/conversation tool.
- Adds DB models for captured intents, resolution results, and staged actions.
- Adds `/parker` FastAPI routes for tick, resurface, confirm, and execute.
- Limits v0 execution to reversible reminder actions and blocks/rejects unsafe/non-reversible actions.
- Adds focused Parker tests covering tool persistence, resolution/staging/resurfacing, confirmation/execution, non-reversible rejection, and API endpoints.

Review notes for feature commit:

- Good review shape: one coherent feature commit with tests included.
- Safety posture is conservative for v0: only reversible reminders execute after confirmation.
- Main caveat for human review: DB schema is model/create-tables based, not Alembic-migrated; reviewers should confirm this is acceptable for the current v0 repo state.
- API error behavior is still thin: missing staged action currently raises from pipeline rather than returning a typed 404 response.
- Resurfacing is read-with-side-effect: `GET /parker/resurface` increments `resurface_count` and updates `last_resurfaced_at`; worth confirming this is intentional.

## Uncommitted cleanup/docs changes

Files:

- `.gitignore`: adds `.venv` alongside existing `.venv/` and `.venv*/` patterns.
- `Makefile`: standardizes backend commands on `backend/.venv`, adds `backend-venv`, Python 3.11 guard, and root-level `make test` / `make run` behavior.
- `README.md`: documents Python 3.11 backend venv setup and root Makefile commands.

Review notes for cleanup/docs:

- These changes are useful but separable from the Parker feature.
- They should likely become a separate cleanup/docs commit before PR review, so the Parker feature diff stays focused.
- The `.gitignore` `.venv` addition appears redundant with `.venv/` and `.venv*/`; harmless, but can be dropped if minimizing noise.

## Tests run

Command:

```bash
cd backend && ./.venv/bin/pytest -v
```

Result:

- `71 passed, 2 warnings in 0.57s`
- Python: `3.11.14`
- Warnings observed:
  - `audioop` deprecation for Python 3.13 in `app/voice/stream.py`
  - Starlette/FastAPI TestClient deprecation re: `httpx2`

## Recommended next action

Prepare for PR, but split first:

1. Keep `3011053` as the Parker feature commit.
2. Commit `.gitignore`, `Makefile`, `README.md`, and this review prep note separately as docs/dev-env cleanup if desired.
3. Ask Claw/human review on the Parker feature diff, with special attention to schema/migration expectations, GET side effects, and 404/error semantics.
4. Do not push/open PR until Prasith explicitly approves tomorrow.
