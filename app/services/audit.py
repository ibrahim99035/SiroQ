"""Audit logging helpers for the Data Explorer (Silver-layer edits & reverts).

Every edit writes a NEW ``edit_audit_log`` row describing old/new values BEFORE
the underlying Silver row is updated. Reverting creates another audit row that
restores the previous value; the historical trail is never deleted.
"""
import uuid
from datetime import datetime, timezone


def log_edit(db, association_id: str, user_id: str, entity_type: str,
             entity_id: str, field: str, old_value, new_value, reason: str | None = None):
    from app.models.models import EditAuditLog
    row = EditAuditLog(
        id=str(uuid.uuid4()),
        association_id=association_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        field=field,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
        user_id=user_id,
        edited_at=datetime.now(timezone.utc),
        reason=reason,
    )
    db.add(row)
    db.flush()
    return row


def recent_edits(db, association_id: str, limit: int = 50):
    from app.models.models import EditAuditLog
    return (
        db.query(EditAuditLog)
        .filter(EditAuditLog.association_id == association_id)
        .order_by(EditAuditLog.edited_at.desc())
        .limit(limit)
        .all()
    )