"""변경 이력(audit log) 기록 유틸."""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import AuditLog

logger = logging.getLogger("ai_callcenter.audit")


def log_middleware(
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
):
    """DB 세션 없이 호출 가능한 미들웨어용 audit 기록.

    자체 SessionLocal 을 열어 1건 기록 후 닫는다. 실패해도 요청 흐름에 영향 X.
    미들웨어(CSRF/IpAllowlist 차단 등) 에서만 사용 — 라우터에서는 log() 를 씀.
    """
    from .database import SessionLocal
    db = SessionLocal()
    try:
        row = AuditLog(
            actor_id=None,
            actor_name="system",
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        logger.warning("audit log (middleware) 기록 실패: %s", exc)
        try: db.rollback()
        except Exception: pass
    finally:
        db.close()


def log(
    db: Session,
    actor,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict | None = None,
    commit: bool = True,
):
    """행위 1건을 audit_logs 에 기록한다.

    actor: AgentUser | None (None 이면 시스템)
    action: "user.create" 등 dot-구분 키
    target_type / target_id: 대상 식별
    details: 자유 JSON
    commit: True 면 즉시 commit (다른 트랜잭션과 묶을 땐 False)
    """
    try:
        row = AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_name=getattr(actor, "username", None) or "system",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        if commit:
            db.commit()
    except Exception as exc:
        logger.warning("audit log 기록 실패: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
