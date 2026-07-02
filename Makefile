.PHONY: backend-venv install run test eval-tasks eval-interactivity eval-demo-interactivity eval-degraded-input-replay eval-caregiver-state-legibility eval-claim-metric-map eval-construct-validity eval-repair-quality-rubric eval-audio-autodata eval-audio-real eval-release-readiness eval-repair eval-brain-lane eval-hands reset-db repl demo voice-deps demo-voice talk talk-loop

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
	python3 benchmark/evaluate_tasks_v0.py --write-report

eval-interactivity:
	python3 benchmark/evaluate_interactivity_v0.py

# Parker-generated trace eval: builds predictions from the current local demo
# and pipeline surfaces, then scores them without overwriting reference reports.
eval-demo-interactivity: backend-venv
	$(BACKEND_PYTHON) benchmark/demo_interactivity_predictions_v0.py --write-report

# Degraded-input replay check: compares the current Parker repair
# protocol against a non-interactive no-repair baseline and a stronger one-shot
# keyword baseline on synthetic held-out effortful-speech transcript fixtures.
eval-degraded-input-replay: backend-venv
	$(BACKEND_PYTHON) benchmark/evaluate_degraded_input_replay_v0.py --write-report

# Caregiver-state legibility proxy: checks whether review UI/state cards make
# pending/queued/approved/cancelled/candidate/safety-contract status identifiable
# versus a raw chat-only baseline. Synthetic proxy, not a human usability study.
eval-caregiver-state-legibility:
	python3 benchmark/evaluate_caregiver_state_legibility_v0.py --write-report

# Release overclaim guard: every public claim (README/launch post) must point
# at emitted metric evidence, a baseline, a safety gate, and a caveat.
eval-claim-metric-map:
	python3 benchmark/evaluate_claim_metric_map_v0.py --write-report

# Construct-validity matrix guard: distinguishes current citable synthetic/local
# evidence from open research gaps so public release copy does not overclaim.
eval-construct-validity:
	python3 benchmark/evaluate_construct_validity_matrix_v0.py --write-report

# Repair-quality proxy rubric: catches generic repair choices and keeps them out
# of citable quality claims. This is synthetic/static, not human-graded evidence.
eval-repair-quality-rubric:
	python3 benchmark/evaluate_repair_quality_rubric_v0.py --write-report

# Audio Autodata repair fixtures: validates metadata-only public/synthetic
# audio-derived ASR failure cases and their safe repair/confirmation targets.
eval-audio-autodata:
	python3 benchmark/evaluate_audio_repair_autodata_v0.py --write-report

# Real-audio eval: run manifest audio through local ASR and TextSession
# routing, scored against each clip's oracle-transcript path. Audio lives in
# the Operations artifacts dir (never this repo); reports are aggregate-only.
# PERSONAL_LEXICON defaults to the synthetic corpus's family names because a
# configured lexicon is standard pilot setup (see docs/runbook.md); pass
# PERSONAL_LEXICON="" for the no-lexicon ablation.
PARKER_AUDIO_ARTIFACTS_DIR ?= $(HOME)/Operations/parker-autodata-nightly
PERSONAL_LEXICON ?= Sarah, Michael, Priya, Anna
eval-audio-real: backend-venv
	PARKER_AUDIO_ARTIFACTS_DIR=$(PARKER_AUDIO_ARTIFACTS_DIR) PERSONAL_LEXICON="$(PERSONAL_LEXICON)" $(BACKEND_PYTHON) benchmark/audio_harness/run.py --models $(or $(MODELS),tiny) --nbest-with $(or $(NBEST),tiny) $(if $(EXTRA_MANIFEST),--extra-manifest $(EXTRA_MANIFEST),) --write-report

# Generate the degraded synthetic command corpus (macOS say; audio lands in
# the Operations artifacts dir, never this repo). Deterministic re-runs.
gen-synthetic-commands: backend-venv
	PARKER_AUDIO_ARTIFACTS_DIR=$(PARKER_AUDIO_ARTIFACTS_DIR) $(BACKEND_PYTHON) benchmark/audio_harness/generate_synthetic.py

# Release readiness rollup: one evidence gate above the individual
# synthetic/local evals. This refreshes every source report first so stale
# public-claim metrics fail closed instead of surviving from an older run.
eval-release-readiness: eval-tasks eval-demo-interactivity eval-degraded-input-replay eval-caregiver-state-legibility eval-claim-metric-map eval-construct-validity eval-repair-quality-rubric
	python3 benchmark/evaluate_release_readiness_v0.py --write-report

# Repair-choice quality eval: runs effortful-speech fixtures through the real
# Claude haiku model and prints candidates for human review. Requires
# ANTHROPIC_API_KEY; skips gracefully when unset.
eval-repair:
	python3 benchmark/evaluate_repair_v0.py

# Brain-lane safety + quality eval: conversational red-team routing runs
# keyless (deterministic guards must refuse before any model); the live
# informational/quality lane needs ANTHROPIC_API_KEY and skips gracefully.
# Unsafe answers are a hard 0 gate (non-zero exit).
eval-brain-lane: backend-venv
	$(BACKEND_PYTHON) benchmark/evaluate_brain_lane_v0.py --write-report

# Hands-lane eval: proposal -> patient confirmation -> OpenClaw skill
# execution over a fake gateway, plus the capability trust-model edges
# (off-allowlist gating, unknown action types, mid-execution gateway
# errors, purchase skills). Keyless and offline; unsafe is a hard 0 gate.
eval-hands: backend-venv
	$(BACKEND_PYTHON) benchmark/evaluate_hands_v0.py --write-report

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
# carries across turns. Parker answers out loud (macOS say) and recording is
# VAD end-pointed: SECONDS is the max window, a natural pause ends the turn.
# Leave this running in a terminal; open the review page as the caregiver.
talk-loop: backend-venv
	cd backend && ./.venv/bin/python -m app.demo.talk_loop $(or $(SECONDS),12)
