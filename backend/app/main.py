"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db.database import create_tables
from app.calls.router import router as calls_router
from app.dashboard.router import router as dashboard_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB tables, start scheduler."""
    create_tables()
    # TODO: start APScheduler for call scheduling
    yield
    # Shutdown


app = FastAPI(
    title="ParkinsClaw",
    description="Voice-first Parkinson's companion",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(calls_router, prefix="/calls", tags=["calls"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])


@app.get("/health")
async def health():
    return {"status": "ok", "patient": settings.patient_name}
