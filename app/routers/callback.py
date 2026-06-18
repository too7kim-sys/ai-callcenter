"""콜백 큐 — 영업시간 외/즉시 응대 불가 고객의 통화 예약.

흐름:
  1) 고객(익명) POST /api/callbacks         — 예약 등록 (rate-limited)
  2) 상담원 GET  /api/callbacks?status=...  — 큐 조회
  3) 상담원 POST /api/callbacks/{id}/contact   — 통화 시작 표시
                 /api/callbacks/{id}/complete  — 완료
                 /api/callbacks/{id}/cancel    — 취소

새 콜백은 외부 알림(Slack/Teams) + 실시간 SSE 로 즉시 푸시된다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import audit, auth, notifier, ratelimit, realtime
from ..database import get_db
from ..models import AgentUser, Callback, Conversation
from ..permissions import P
from ..schemas import CallbackCreate, CallbackUpdate

router = APIRouter(prefix="/api/callbacks", tags=["callback"])

_STATUSES = {"pending", "contacted", "completed", "cancelled"}


def _serialize(cb: Callback, db: Session) -> dict:
    agent = db.get(AgentUser, cb.assigned_agent_id) if cb.assigned_agent_id else None
    return {
        "id": cb.id,
        "conversation_id": cb.conversation_id,
        "customer_name": cb.customer_name,
        "phone": cb.phone,
        "preferred_text": cb.preferred_text,
        "note": cb.note,
        "status": cb.status,
        "assigned_agent_id": cb.assigned_agent_id,
        "assigned_agent_name": (agent.name or agent.username) if agent else None,
        "created_at": cb.created_at.isoformat() if cb.created_at else None,
        "contacted_at": cb.contacted_at.isoformat() if cb.contacted_at else None,
        "completed_at": cb.completed_at.isoformat() if cb.completed_at else None,
    }


@router.post(
    "",
    dependencies=[Depends(ratelimit.rate_limit("callback_create", 3, 60))],
)
def create_callback(payload: CallbackCreate, db: Session = Depends(get_db)):
    """고객(익명)이 콜백 예약. IP 당 3건/분 제한."""
    name = (payload.customer_name or "고객").strip() or "고객"
    cb = Callback(
        customer_name=name,
        phone=payload.phone.strip(),
        preferred_text=(payload.preferred_text or "").strip() or None,
        note=(payload.note or "").strip() or None,
        conversation_id=payload.conversation_id,
    )
    db.add(cb)
    db.commit()
    db.refresh(cb)

    # 외부 알림 + 실시간 푸시
    notifier.notify(
        title="콜백 예약 접수",
        body=(
            f"*{name}* / {cb.phone}\n"
            f"> 희망 시간: {cb.preferred_text or '(미지정)'}\n"
            + (f"> 메모: {cb.note}" if cb.note else "")
        ),
        severity="warning",
        conversation_id=cb.conversation_id,
    )
    realtime.publish({"type": "callback_created", "callback_id": cb.id})

    return _serialize(cb, db)


@router.get("")
def list_callbacks(
    status: str | None = None,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CALLBACK_MANAGE)),
):
    """콜백 큐 조회. status 미지정 시 pending+contacted (활성) 만."""
    q = db.query(Callback)
    if status:
        if status not in _STATUSES:
            raise HTTPException(status_code=400, detail="유효하지 않은 상태입니다.")
        q = q.filter(Callback.status == status)
    else:
        q = q.filter(Callback.status.in_(("pending", "contacted")))
    q = q.order_by(Callback.created_at.desc())
    return [_serialize(cb, db) for cb in q.all()]


@router.get("/summary")
def callback_summary(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CALLBACK_MANAGE)),
):
    """대시보드 용 — 상태별 카운트."""
    from sqlalchemy import func
    rows = (
        db.query(Callback.status, func.count(Callback.id))
        .group_by(Callback.status)
        .all()
    )
    by_status = {s: n for s, n in rows}
    return {
        "pending": by_status.get("pending", 0),
        "contacted": by_status.get("contacted", 0),
        "completed": by_status.get("completed", 0),
        "cancelled": by_status.get("cancelled", 0),
    }


def _transition(cb: Callback, new_status: str, user: AgentUser):
    """상태 전이 + 타임스탬프 설정. 무효 전이는 400."""
    if new_status not in _STATUSES:
        raise HTTPException(status_code=400, detail="유효하지 않은 상태입니다.")
    cb.status = new_status
    cb.assigned_agent_id = user.id
    now = datetime.now(timezone.utc)
    if new_status == "contacted":
        cb.contacted_at = now
    elif new_status == "completed":
        if cb.contacted_at is None:
            cb.contacted_at = now
        cb.completed_at = now
    elif new_status == "cancelled":
        cb.completed_at = now


@router.post("/{cb_id}/contact")
def mark_contacted(
    cb_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CALLBACK_MANAGE)),
):
    cb = db.get(Callback, cb_id)
    if cb is None:
        raise HTTPException(status_code=404, detail="콜백을 찾을 수 없습니다.")
    _transition(cb, "contacted", user)
    db.commit()
    audit.log(db, user, "callback.contact", target_type="callback", target_id=cb.id)
    realtime.publish({"type": "callback_updated", "callback_id": cb.id, "status": "contacted"})
    db.refresh(cb)
    return _serialize(cb, db)


@router.post("/{cb_id}/complete")
def mark_completed(
    cb_id: int,
    payload: CallbackUpdate | None = None,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CALLBACK_MANAGE)),
):
    cb = db.get(Callback, cb_id)
    if cb is None:
        raise HTTPException(status_code=404, detail="콜백을 찾을 수 없습니다.")
    _transition(cb, "completed", user)
    if payload and payload.note:
        cb.note = (cb.note + "\n" if cb.note else "") + payload.note.strip()
    db.commit()
    audit.log(db, user, "callback.complete", target_type="callback", target_id=cb.id)
    realtime.publish({"type": "callback_updated", "callback_id": cb.id, "status": "completed"})
    db.refresh(cb)
    return _serialize(cb, db)


@router.post("/{cb_id}/cancel")
def cancel_callback(
    cb_id: int,
    payload: CallbackUpdate | None = None,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CALLBACK_MANAGE)),
):
    cb = db.get(Callback, cb_id)
    if cb is None:
        raise HTTPException(status_code=404, detail="콜백을 찾을 수 없습니다.")
    _transition(cb, "cancelled", user)
    if payload and payload.note:
        cb.note = (cb.note + "\n" if cb.note else "") + f"(취소 사유: {payload.note.strip()})"
    db.commit()
    audit.log(db, user, "callback.cancel", target_type="callback", target_id=cb.id)
    realtime.publish({"type": "callback_updated", "callback_id": cb.id, "status": "cancelled"})
    db.refresh(cb)
    return _serialize(cb, db)
