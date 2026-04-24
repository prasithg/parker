"""Call scheduling and initiation endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/trigger")
async def trigger_call(call_type: str = "check_in"):
    """Manually trigger an outbound call. TODO: implement."""
    return {"status": "not_implemented", "call_type": call_type}


@router.get("/history")
async def call_history(limit: int = 20):
    """Return recent call logs. TODO: implement."""
    return {"status": "not_implemented"}
