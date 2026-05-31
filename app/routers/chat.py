"""고객 채팅 API: 상담 생성, AI 챗봇 응답, 상담 조회."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datetime import datetime

from .. import ai, config, knowledge
from ..database import get_db
from ..models import Conversation, Message
from ..schemas import (
    AgentRequestBody,
    ChatRequest,
    ConversationCreate,
    CustomerEndRequest,
    FeedbackRequest,
)
from ..service import (
    build_history,
    get_conversation_or_404,
    now,
    serialize_conversation,
)


def _business_hours_active() -> bool:
    """현재 시각이 영업 시간(평일 09~18시) 내인지."""
    nowt = datetime.now()
    if nowt.weekday() >= 5:  # 토(5), 일(6)
        return False
    return config.BUSINESS_START_HOUR <= nowt.hour < config.BUSINESS_END_HOUR

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
    return serialize_conversation(conv, include_messages=True, db=db)


@router.get("/business-status")
def business_status():
    """영업 시간 안내 (고객 채팅 진입 시 배너)."""
    active = _business_hours_active()
    return {
        "active": active,
        "start_hour": config.BUSINESS_START_HOUR,
        "end_hour": config.BUSINESS_END_HOUR,
        "message": (
            f"상담원 응대 시간 (평일 {config.BUSINESS_START_HOUR}:00 ~ "
            f"{config.BUSINESS_END_HOUR}:00). 시간 외에는 AI 상담봇이 응대합니다."
        ) if not active else None,
    }


@router.post("/messages/{message_id}/feedback")
def feedback_message(
    message_id: int,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
):
    """고객이 AI/상담원 답변에 👍/👎 피드백 (공개). 부정 피드백 누적 시 자동 에스컬레이션."""
    msg = db.get(Message, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    if msg.role not in ("ai", "agent"):
        raise HTTPException(status_code=400, detail="고객 메시지에는 평가할 수 없습니다.")
    v = (payload.value or "").strip().lower()
    msg.feedback = v if v in ("up", "down") else None
    db.commit()
    # 부정 피드백이면 해당 상담을 에스컬레이션 후보로 표시
    if msg.feedback == "down":
        conv = db.get(Conversation, msg.conversation_id)
        if conv and conv.status == "open":
            conv.status = "escalated"
            conv.updated_at = now()
            db.commit()
    return {"ok": True, "feedback": msg.feedback}


@router.post("/conversations/{conversation_id}/agent-request")
def request_agent(
    conversation_id: int,
    payload: AgentRequestBody,
    db: Session = Depends(get_db),
):
    """고객이 명시적으로 '상담원 연결' 요청. 상태를 에스컬레이션으로 전환."""
    conv = get_conversation_or_404(db, conversation_id)
    conv.agent_requested = True
    if conv.status == "open":
        conv.status = "escalated"
    conv.updated_at = now()
    note = "(고객 상담원 연결 요청)"
    if payload.note.strip():
        note += f" {payload.note.strip()[:300]}"
    sys_msg = Message(conversation_id=conv.id, role="ai", content=note)
    db.add(sys_msg)
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True, db=db)


@router.post("/conversations/{conversation_id}/end")
def end_conversation(
    conversation_id: int,
    payload: CustomerEndRequest,
    db: Session = Depends(get_db),
):
    """고객이 상담 종료 + (선택) 만족도 평가."""
    conv = get_conversation_or_404(db, conversation_id)
    if payload.rating is not None:
        conv.customer_rating = payload.rating
    if payload.feedback:
        conv.customer_feedback = payload.feedback.strip()[:1000]
    conv.status = "closed"
    conv.updated_at = now()
    db.commit()
    # 종료 시 학습 (관리자/상담원의 close 와 동일한 흐름)
    knowledge.learn_from_conversation(db, conv)
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True, db=db)


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

    # 3) 학습된 과거 상담 사례 검색 후 AI 챗봇 멀티턴 응답 생성
    past_cases = knowledge.retrieve(db, message, limit=3)
    reply = ai.generate_reply(build_history(conv), past_cases=past_cases)
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
