"""WebRTC 음성 통화 — 시그널링 + 라이프사이클.

흐름:
  1) 고객(채팅 페이지/위젯)이 ☎ 버튼 → POST /api/calls/request
       → Call(status='requesting') 생성, SSE 'call_requested' 전체 브로드캐스트
  2) 상담원 콘솔이 SSE 로 받음 → '받기' 버튼 → POST /api/calls/{id}/answer
       → Call.status='answered', SSE 'call_answered' (해당 상담만)
  3) 양측 브라우저가 WebRTC PeerConnection 시작
       → POST /api/calls/{id}/signal {kind: 'offer'/'answer'/'ice', payload}
       → 같은 conversation 의 반대편으로 SSE 'call_signal' 중계
  4) 음성은 P2P 로 직접 흐름 (서버 미경유)
  5) 어느 쪽이든 끊기 → POST /api/calls/{id}/end
       → 통화 시간 계산, SSE 'call_ended'

비고:
  • 첫 응답자만 답변 가능 (이미 answered/ended 면 409)
  • Customer 호출은 익명 + rate-limited
  • 시그널링 payload 는 client → server → SSE 로 그대로 통과 (서버는 내용 미해석)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit, auth, ratelimit, realtime
from ..database import get_db
from ..models import AgentSession, AgentUser, Call, Conversation
from ..permissions import P
from ..schemas import CallEndRequest, CallRequest, CallSignalRequest

router = APIRouter(prefix="/api/calls", tags=["calls"])

# 무응답으로 90초 이상 잔존한 requesting 통화 → missed 자동 종료.
# /active, /request, /answer 핸들러가 호출되는 시점에 lazy 정리.
_REQUESTING_TIMEOUT_SECS = 90


def _sweep_stale(db: Session) -> int:
    """오래된 requesting 통화 자동 종료. 정리한 건수 반환."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_REQUESTING_TIMEOUT_SECS)
    stale = (
        db.query(Call)
          .filter(Call.status == "requesting", Call.requested_at < cutoff)
          .all()
    )
    if not stale:
        return 0
    now = datetime.now(timezone.utc)
    for call in stale:
        call.status = "ended"
        call.ended_at = now
        call.end_reason = "missed"
    db.commit()
    for call in stale:
        realtime.publish({
            "type": "call_ended",
            "call_id": call.id,
            "conversation_id": call.conversation_id,
            "reason": "missed",
        })
    return len(stale)


def _serialize(call: Call, db: Session) -> dict:
    agent = db.get(AgentUser, call.assigned_agent_id) if call.assigned_agent_id else None
    return {
        "id": call.id,
        "conversation_id": call.conversation_id,
        "status": call.status,
        "customer_name": call.customer_name,
        "assigned_agent_id": call.assigned_agent_id,
        "assigned_agent_name": (agent.name or agent.username) if agent else None,
        "requested_at": call.requested_at.isoformat() if call.requested_at else None,
        "answered_at": call.answered_at.isoformat() if call.answered_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_seconds": call.duration_seconds,
        "end_reason": call.end_reason,
    }


@router.post(
    "/request",
    dependencies=[Depends(ratelimit.rate_limit("call_request", 5, 60))],
)
def request_call(payload: CallRequest, db: Session = Depends(get_db)):
    """고객(익명)이 통화 요청. IP 당 5/분."""
    _sweep_stale(db)
    conv = db.get(Conversation, payload.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="상담을 찾을 수 없습니다.")

    # 같은 conversation 에 이미 활성 통화가 있으면 그것을 반환 (중복 호출 방어)
    existing = (
        db.query(Call)
          .filter(Call.conversation_id == conv.id,
                  Call.status.in_(("requesting", "answered")))
          .order_by(Call.id.desc())
          .first()
    )
    if existing is not None:
        return _serialize(existing, db)

    name = (payload.customer_name or conv.customer_name or "고객").strip() or "고객"
    call = Call(conversation_id=conv.id, customer_name=name)
    db.add(call)
    db.commit()
    db.refresh(call)

    realtime.publish({
        "type": "call_requested",
        "call_id": call.id,
        "conversation_id": conv.id,
        "customer_name": name,
    })
    return _serialize(call, db)


@router.post("/{call_id}/answer")
def answer_call(
    call_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """상담원이 통화 응답. 첫 응답자만 성공 (이미 답변/종료 시 409)."""
    _sweep_stale(db)
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="통화를 찾을 수 없습니다.")
    if call.status != "requesting":
        raise HTTPException(status_code=409, detail=f"이미 처리된 통화입니다 (상태: {call.status})")
    call.status = "answered"
    call.assigned_agent_id = user.id
    call.answered_at = datetime.now(timezone.utc)
    db.commit()
    audit.log(db, user, "call.answer", target_type="call", target_id=call.id)
    realtime.publish({
        "type": "call_answered",
        "call_id": call.id,
        "conversation_id": call.conversation_id,
        "agent_id": user.id,
        "agent_name": user.name or user.username,
    })
    db.refresh(call)
    return _serialize(call, db)


@router.post("/{call_id}/reject")
def reject_call(
    call_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """상담원이 명시적으로 거절 — 통화를 missed 로 종료."""
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="통화를 찾을 수 없습니다.")
    if call.status != "requesting":
        raise HTTPException(status_code=409, detail="이미 처리된 통화입니다.")
    call.status = "ended"
    call.ended_at = datetime.now(timezone.utc)
    call.end_reason = "rejected"
    db.commit()
    audit.log(db, user, "call.reject", target_type="call", target_id=call.id)
    realtime.publish({
        "type": "call_ended",
        "call_id": call.id,
        "conversation_id": call.conversation_id,
        "reason": "rejected",
    })
    return {"ok": True}


@router.post("/{call_id}/signal")
def signal_call(
    call_id: int,
    payload: CallSignalRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """WebRTC 시그널링 중계 (offer / answer / ICE candidate / hangup).

    서버는 payload 내용을 해석하지 않고 같은 conversation 의 반대편으로
    SSE 'call_signal' 이벤트로 그대로 forward. 'from' 필드는 세션 쿠키
    유무로 자동 판별: 인증 쿠키가 있으면 'agent', 없으면 'customer'.
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="통화를 찾을 수 없습니다.")
    if call.status not in ("requesting", "answered"):
        raise HTTPException(status_code=409, detail="활성 통화가 아닙니다.")

    role = _from_role(request, db)
    realtime.publish({
        "type": "call_signal",
        "call_id": call.id,
        "conversation_id": call.conversation_id,
        "from": role,
        "kind": payload.kind,
        "payload": payload.payload,
    })
    return {"ok": True}


@router.post("/{call_id}/end")
def end_call(
    call_id: int,
    payload: CallEndRequest | None = None,
    db: Session = Depends(get_db),
):
    """어느 쪽이든 통화 종료. 통화 시간 계산 + 모두에게 종료 알림."""
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="통화를 찾을 수 없습니다.")
    if call.status == "ended":
        return _serialize(call, db)

    now = datetime.now(timezone.utc)
    if call.answered_at:
        ans = call.answered_at.replace(tzinfo=None) if call.answered_at.tzinfo else call.answered_at
        n = now.replace(tzinfo=None)
        call.duration_seconds = max(0, int((n - ans).total_seconds()))
    call.status = "ended"
    call.ended_at = now
    if payload and payload.reason:
        call.end_reason = payload.reason[:40]
    elif not call.end_reason:
        call.end_reason = "hangup"
    db.commit()
    realtime.publish({
        "type": "call_ended",
        "call_id": call.id,
        "conversation_id": call.conversation_id,
        "duration_seconds": call.duration_seconds,
        "reason": call.end_reason,
    })
    return _serialize(call, db)


@router.get("/active")
def list_active_calls(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """현재 벨림(requesting) + 통화중(answered) 목록."""
    _sweep_stale(db)
    rows = (
        db.query(Call)
          .filter(Call.status.in_(("requesting", "answered")))
          .order_by(Call.requested_at.desc())
          .all()
    )
    return [_serialize(c, db) for c in rows]


@router.get("/recent")
def list_recent_calls(
    limit: int = 50,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """최근 통화 이력 — 통화 시간/응답 상담원/사유."""
    limit = max(1, min(200, int(limit)))
    rows = (
        db.query(Call)
          .order_by(Call.requested_at.desc())
          .limit(limit)
          .all()
    )
    return [_serialize(c, db) for c in rows]


def _from_role(request: Request, db: Session) -> str:
    """/signal 호출자의 'from' 라벨 — 인증 쿠키 보유 시 'agent', 아니면 'customer'.

    예외를 던지지 않는 가벼운 검증 (current_user 의존성과 분리해 익명 호출도 허용).
    """
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not token:
        return "customer"
    sess = db.query(AgentSession).filter(AgentSession.token == token).first()
    if sess is None or sess.expires_at is None:
        return "customer"
    expires = sess.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return "customer"
    return "agent"
