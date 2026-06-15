from fastapi import APIRouter, Path, Query

router = APIRouter()


@router.post("/webhooks/luxalgo/{strategy_id}")
async def receive_luxalgo_webhook(
    strategy_id: str = Path(..., description="Strategy identifier from URL path"),
    token: str = Query(..., description="Webhook authentication token"),
) -> dict:
    # Phase 1 stub — returns 200, saves nothing yet.
    # strategy_id ALWAYS from URL path, never from payload (critical rule).
    return {"received": True, "strategy_id": strategy_id}
