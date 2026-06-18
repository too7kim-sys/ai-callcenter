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


def finalize_conversation(conversation_id: int, *, force_summary: bool = False):
    """상담 종료 후처리 — AI 자동 요약·분류 + RAG 학습.

    백그라운드 태스크용. 자체 DB 세션을 열어 본 요청 응답을 막지 않는다.
    AI 호출이 수 초 걸릴 수 있으나 사용자는 이미 종료 응답을 받은 뒤다.

    실패해도 사용자에게 오류를 노출하지 않고 로그만 남긴다 — 상담 자체는
    이미 닫혔으므로 후처리 실패가 상담 종료를 막아서는 안 된다.
    """
    from . import ai, knowledge
    from .database import SessionLocal

    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return

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
            except Exception as exc:
                db.rollback()
                logger.warning("상담 #%s 자동 요약 실패: %s", conv.id, exc)

        # 2) RAG 학습 — 상담원 답변 쌍을 KnowledgeItem 에 적재
        try:
            learned = knowledge.learn_from_conversation(db, conv)
            logger.info("상담 #%s 자동 학습: %s 항목", conv.id, learned)
        except Exception as exc:
            db.rollback()
            logger.warning("상담 #%s 자동 학습 실패: %s", conv.id, exc)
    finally:
        db.close()


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


def serialize_conversation(conv, include_messages=False, db=None):
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
    # 배정 상담원 이름 조회 (있을 때만)
    if db is not None and data["assigned_agent_id"]:
        from .models import AgentUser
        u = db.get(AgentUser, data["assigned_agent_id"])
        if u:
            data["assigned_agent_name"] = u.name or u.username
    if include_messages:
        data["messages"] = [serialize_message(m) for m in messages]
    return data
