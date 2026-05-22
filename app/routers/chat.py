"""고객 채팅 API: 상담 생성, AI 챗봇 응답, 상담 조회."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import ai
from ..database import get_db
from ..models import Conversation, Message
from ..schemas import ChatRequest, ConversationCreate
from ..service import (
    build_history,
    get_conversation_or_404,
    now,
    serialize_conversation,
)

router = APIRouter(prefix="/api", tags=["customer"])


@router.post("/conversations")
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)):
    """새 상담 시작."""
    conv = Conversation(customer_name=(payload.customer_name or "고객").strip() or "고객")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    """상담 상세 + 메시지 조회 (고객/상담원 공용)."""
    conv = get_conversation_or_404(db, conversation_id)
    return serialize_conversation(conv, include_messages=True)


@router.post("/conversations/{conversation_id}/chat")
def chat(conversation_id: int, payload: ChatRequest, db: Session = Depends(get_db)):
    """고객 메시지 수신 → 감정 분석 → AI 챗봇 자동 응답."""
    conv = get_conversation_or_404(db, conversation_id)
    message = payload.message.strip()

    # 1) 고객 메시지 저장 + 감정 분석
    sentiment = ai.analyze_sentiment(message)
    customer_msg = Message(
        conversation_id=conv.id,
        role="customer",
        content=message,
        sentiment=sentiment["sentiment"],
        sentiment_score=sentiment["score"],
    )
    db.add(customer_msg)

    # 2) 상담 단위 감정 상태 갱신 + 위험 상담 에스컬레이션
    conv.sentiment = sentiment["sentiment"]
    conv.sentiment_score = sentiment["score"]
    conv.risk_level = sentiment["risk_level"]
    if sentiment["risk_level"] == "high" and conv.status != "closed":
        conv.status = "escalated"
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)

    # 3) AI 챗봇 멀티턴 응답 생성
    reply = ai.generate_reply(build_history(conv))
    ai_msg = Message(conversation_id=conv.id, role="ai", content=reply["reply"])
    db.add(ai_msg)
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)

    return {
        "conversation": serialize_conversation(conv, include_messages=True),
        "sentiment": sentiment,
        "ai_source": reply["source"],
    }
