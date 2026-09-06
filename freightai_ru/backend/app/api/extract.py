"""AI extraction endpoint -- structured-data-only, rate-limited
separately so one user can't drain the AI budget (see services/rate_limit.py)."""
from fastapi import APIRouter, Depends

from app.core.security import verify_bot_secret
from app.schemas.schemas import ExtractIn
from app.services.extraction import extract_from_text
from app.services.rate_limit import check_and_increment

router = APIRouter(prefix="/internal/extract", tags=["extract"], dependencies=[Depends(verify_bot_secret)])


@router.post("")
async def extract(payload: ExtractIn):
    await check_and_increment(payload.telegram_id, "ai_extract")
    result = await extract_from_text(payload.text)
    return result.model_dump()
