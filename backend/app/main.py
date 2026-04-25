"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from app.calls.router import router as calls_router
from app.calls.scheduler import setup_scheduler
from app.config import settings
from app.dashboard.router import router as dashboard_router
from app.db.database import SessionLocal, create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB tables, start scheduler."""

    create_tables()
    scheduler = BackgroundScheduler(timezone="America/New_York")
    setup_scheduler(scheduler, SessionLocal)
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="ParkinsClaw",
    description="Voice-first Parkinson's companion",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(calls_router, prefix="/calls", tags=["calls"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])


@app.get("/health")
async def health():
    return {"status": "ok", "patient": settings.patient_name}
