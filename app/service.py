"""라우터 공용 헬퍼: 직렬화 및 조회."""
import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from .models import Conversation, Message

logger = logging.getLogger("ai_callcenter.service")


def now():
    return datetime.now(timezone.utc)


def get_conversation_or_404(db, conversation_id):
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="상담을 찾을 수 없습니다.")
    return conv


def build_history(conv):
    """AI 모듈에 전달할 대화 이력 형식으로 변환."""
    return [{"role": m.role, "content": m.content} for m in conv.messages]


def finalize_conversation(conversation_id: int, *, force_summary: bool = False) -> dict:
    """상담 종료 후처리 — AI 자동 요약·분류 + RAG 학습.

    백그라운드 태스크용. 자체 DB 세션을 열어 본 요청 응답을 막지 않는다.
    AI 호출이 수 초 걸릴 수 있으나 사용자는 이미 종료 응답을 받은 뒤다.

    실패는 audit log 에 'conversation.finalize_failed' 로 기록되어
    /api/admin/conversations/pending-summary 와 finalize-pending 으로 가시화·
    재시도 가능. 상담 종료 자체는 이미 끝났으므로 후처리 실패가 종료를 막진 않음.

    반환: {summarized, learned, error?} — 호출자가 결과 확인 가능.
    """
    from . import ai, audit, knowledge
    from .database import SessionLocal

    result_meta: dict = {"summarized": False, "learned": 0}
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return {"summarized": False, "learned": 0, "error": "conv_not_found"}

        # 1) 자동 요약·분류 (이미 분석된 상담은 skip, force_summary=True 면 강제)
        if force_summary or not conv.summary:
            try:
                result = ai.summarize(build_history(conv))
                conv.summary = result["summary"]
                conv.category = result["category"]
                conv.tags = json.dumps(result["tags"], ensure_ascii=False)
                conv.key_points = json.dumps(result["key_points"], ensure_ascii=False)
                conv.updated_at = now()
                db.commit()
                logger.info("상담 #%s 자동 요약 완료 (source=%s)", conv.id, result.get("source"))
                result_meta["summarized"] = True
            except Exception as exc:
                db.rollback()
                logger.warning("상담 #%s 자동 요약 실패: %s", conv.id, exc)
                result_meta["error"] = str(exc)[:200]
                # 실패를 영구 기록 — 관리자가 재시도 대상 식별 가능
                try:
                    audit.log(db, None, "conversation.finalize_failed",
                              target_type="conversation", target_id=conv.id,
                              details={"error": str(exc)[:300], "stage": "summarize"})
                except Exception:
                    pass

        # 2) RAG 학습 — 상담원 답변 쌍을 KnowledgeItem 에 적재
        try:
            learned = knowledge.learn_from_conversation(db, conv)
            logger.info("상담 #%s 자동 학습: %s 항목", conv.id, learned)
            result_meta["learned"] = learned
        except Exception as exc:
            db.rollback()
            logger.warning("상담 #%s 자동 학습 실패: %s", conv.id, exc)
            result_meta.setdefault("error", str(exc)[:200])
            try:
                audit.log(db, None, "conversation.finalize_failed",
                          target_type="conversation", target_id=conv.id,
                          details={"error": str(exc)[:300], "stage": "knowledge"})
            except Exception:
                pass
    finally:
        db.close()
    return result_meta


def _parse_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _iso(value):
    return value.isoformat() if value else None


def serialize_message(message):
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "sentiment": message.sentiment,
        "sentiment_score": message.sentiment_score,
        "feedback": getattr(message, "feedback", None),
        "created_at": _iso(message.created_at),
    }


def serialize_conversation(conv, include_messages=False, db=None, agent_map=None):
    """대화 직렬화.

    agent_map: {agent_id: AgentUser} — 목록 직렬화 시 미리 채워 두면 N+1 제거.
               미제공 시 db 가 주어지면 단건 조회 (단일 conv 시 충분).
    """
    messages = list(conv.messages)
    data = {
        "id": conv.id,
        "customer_name": conv.customer_name,
        "channel": conv.channel,
        "status": conv.status,
        "category": conv.category,
        "tags": _parse_list(conv.tags),
        "summary": conv.summary,
        "key_points": _parse_list(conv.key_points),
        "sentiment": conv.sentiment,
        "sentiment_score": conv.sentiment_score,
        "risk_level": conv.risk_level,
        "assigned_agent_id": getattr(conv, "assigned_agent_id", None),
        "assigned_agent_name": None,
        "agent_requested": bool(getattr(conv, "agent_requested", False)),
        "customer_rating": getattr(conv, "customer_rating", None),
        "customer_feedback": getattr(conv, "customer_feedback", None),
        "created_at": _iso(conv.created_at),
        "updated_at": _iso(conv.updated_at),
        "message_count": len(messages),
        "last_message": messages[-1].content if messages else None,
    }
    aid = data["assigned_agent_id"]
    if aid:
        if agent_map is not None:
            u = agent_map.get(aid)
            if u:
                data["assigned_agent_name"] = u.name or u.username
        elif db is not None:
            from .models import AgentUser
            u = db.get(AgentUser, aid)
            if u:
                data["assigned_agent_name"] = u.name or u.username
    if include_messages:
        data["messages"] = [serialize_message(m) for m in messages]
    return data


def load_agent_map(db, conversations):
    """배정된 상담원들을 한 번에 로드해 {id: AgentUser} 반환 — N+1 제거용."""
    from .models import AgentUser
    ids = {c.assigned_agent_id for c in conversations if c.assigned_agent_id}
    if not ids:
        return {}
    rows = db.query(AgentUser).filter(AgentUser.id.in_(ids)).all()
    return {u.id: u for u in rows}
