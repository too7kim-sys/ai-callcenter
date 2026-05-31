"""라우터 공용 헬퍼: 직렬화 및 조회."""
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from .models import Conversation, Message


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
