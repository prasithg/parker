.PHONY: backend-venv install run test eval-tasks eval-repair reset-db repl demo voice-deps demo-voice talk talk-loop

BACKEND_PYTHON := backend/.venv/bin/python
BACKEND_PIP := backend/.venv/bin/pip
BACKEND_UVICORN := backend/.venv/bin/uvicorn
BACKEND_PYTEST := backend/.venv/bin/pytest

backend-venv:
	@if [ -x "$(BACKEND_PYTHON)" ] && [ "$$($(BACKEND_PYTHON) -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')" != "3.11" ]; then \
		echo "backend/.venv exists but is not Python 3.11; move it aside before recreating (for example: mv backend/.venv backend/.venv-python3.9-backup)"; \
		exit 1; \
	fi
	@if [ ! -x "$(BACKEND_PYTHON)" ]; then python3.11 -m venv backend/.venv; fi
	$(BACKEND_PIP) install -r backend/requirements.txt

install: backend-venv
	cd dashboard && npm install

# Run from backend/ so the SQLite file lands in backend/, matching the
# seeding/REPL commands in docs/runbook.md (one DB, deterministic demos).
run: backend-venv
	cd backend && ./.venv/bin/uvicorn app.main:app --reload --port 8000

run-dashboard:
	cd dashboard && npm run dev

test: backend-venv
	cd backend && ./.venv/bin/pytest -v

eval-tasks:
	python3 benchmark/evaluate_tasks_v0.py

# Repair-choice quality eval: runs effortful-speech fixtures through the real
# Claude haiku model and prints candidates for human review. Requires
# ANTHROPIC_API_KEY; skips gracefully when unset.
eval-repair:
	python3 benchmark/evaluate_repair_v0.py

# Deterministic local reset: v0 uses create_tables(), which never ALTERs,
# so schema changes require a fresh DB. Removes both historical locations.
reset-db: backend-venv
	rm -f parkinsclaw.db backend/parkinsclaw.db parker.db backend/parker.db
	cd backend && ./.venv/bin/python -c "from app.db.database import create_tables; create_tables(); print('Fresh local DB created at backend/parker.db')"

repl: backend-venv
	cd backend && ./.venv/bin/python -m app.conversation.textloop

# One-command demo: fresh DB, a believable seeded family day, and a synthetic
# effortful-speech transcript replayed through the text loop. Then `make run`
# and open http://localhost:8000/parker/review/ui as the caregiver.
demo: reset-db
	cd backend && ./.venv/bin/python -m app.demo.seed
	cd backend && ./.venv/bin/python -m app.demo.replay
	@echo ""
	@echo "Demo ready. Start the server with 'make run' and open:"
	@echo "  http://localhost:8000/parker/review/ui"

# Optional, local-only transcription deps (faster-whisper). Not part of the
# core suite — tests inject a fake transcriber. First run downloads model
# weights to the local Hugging Face cache; inference is fully on-device.
voice-deps: backend-venv
	$(BACKEND_PIP) install -r backend/requirements-voice.txt

# Local voice demo: transcribe AUDIO on this machine and feed the transcript
# through the same text-loop routing as `make demo`. The audio file is only
# read — never copied or stored; transcripts are the only artifact.
demo-voice: backend-venv
ifndef AUDIO
	$(error usage: make demo-voice AUDIO=path/to/audio.wav)
endif
	cd backend && ./.venv/bin/python -m app.demo.voice "$(abspath $(AUDIO))"

# Talk to Parker: record SECONDS (default 6) from the default microphone,
# transcribe on this machine, route through the text loop. The recording
# is a temp file deleted right after transcription — transcripts only.
talk: backend-venv
	cd backend && ./.venv/bin/python -m app.demo.talk $(or $(SECONDS),6)

migrate:
	@echo "v0 uses create_tables() — no Alembic yet (use make reset-db for a fresh local DB)"

# Continuous talk loop: one persistent TextSession so repair-choice state
# carries across turns. Leave this running in a terminal; open the review
# page in a browser as the caregiver view. Ctrl-C to stop.
talk-loop: backend-venv
	cd backend && ./.venv/bin/python -m app.demo.talk_loop $(or $(SECONDS),6)
