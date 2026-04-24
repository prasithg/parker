"""Dashboard API endpoints for family view."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/summary")
async def dashboard_summary():
    """Overview: recent calls, adherence rate, mood trend. TODO: implement."""
    return {"status": "not_implemented"}


@router.get("/calls")
async def dashboard_calls(limit: int = 20):
    """Call history with summaries. TODO: implement."""
    return {"status": "not_implemented"}


@router.get("/medications")
async def dashboard_medications():
    """Medication schedule + adherence. TODO: implement."""
    return {"status": "not_implemented"}
