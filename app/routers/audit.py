"""변경 이력(audit log) 조회 API."""
import json as _json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import AuditLog
from ..permissions import P

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit")
def list_audit(
    limit: int = 200,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """최근 변경 이력 (최신 순)."""
    limit = max(1, min(limit, 1000))
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    result = []
    for r in rows:
        details = None
        if r.details:
            try: details = _json.loads(r.details)
            except (ValueError, TypeError): details = r.details
        result.append({
            "id": r.id,
            "actor": r.actor_name or "system",
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "details": details,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return result
