.PHONY: install run test

install:
	cd backend && pip install -r requirements.txt
	cd dashboard && npm install

run:
	cd backend && uvicorn app.main:app --reload --port 8000

run-dashboard:
	cd dashboard && npm run dev

test:
	cd backend && pytest -v

migrate:
	@echo "v0 uses create_tables() — no Alembic yet"
