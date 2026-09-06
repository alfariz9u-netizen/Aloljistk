"""Writes to audit_logs for privileged/sensitive actions (admin actions,
match/contact-info access, cancellations). A failed audit write never
blocks the action it describes -- see log_action()'s try/except -- but
IS logged loudly to the application logger so it's never silently lost."""
import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog

logger = logging.getLogger("freightai")


async def log_action(
    db: AsyncSession,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    try:
        db.add(AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
        ))
    except Exception:
        logger.exception("Failed to queue audit log entry for action=%s", action)
