"""상담원 콘솔 API: 상담 목록, AI 요약·분류, 답변 추천, 답변 전송, 상태 변경."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ai, faq, knowledge
from ..database import get_db
from ..models import Conversation, Message
from ..schemas import ReplyRequest, StatusRequest
from ..service import (
    build_history,
    get_conversation_or_404,
    now,
    serialize_conversation,
)

router = APIRouter(prefix="/api", tags=["agent"])

_STATUSES = {"open", "escalated", "closed"}


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db)):
    """전체 상담 목록 (최근 갱신 순)."""
    conversations = (
        db.query(Conversation).order_by(Conversation.updated_at.desc()).all()
    )
    return [serialize_conversation(c) for c in conversations]


@router.post("/conversations/{conversation_id}/analyze")
def analyze_conversation(conversation_id: int, db: Session = Depends(get_db)):
    """상담 요약·분류: 전체 대화를 요약하고 카테고리/태그를 부여한다."""
    conv = get_conversation_or_404(db, conversation_id)
    result = ai.summarize(build_history(conv))

    conv.summary = result["summary"]
    conv.category = result["category"]
    conv.tags = json.dumps(result["tags"], ensure_ascii=False)
    conv.key_points = json.dumps(result["key_points"], ensure_ascii=False)
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)

    return {
        "conversation": serialize_conversation(conv, include_messages=True),
        "source": result["source"],
    }


@router.post("/conversations/{conversation_id}/recommend")
def recommend_answers(conversation_id: int, db: Session = Depends(get_db)):
    """상담원 답변 추천: FAQ + 학습된 과거 상담 사례 기반 추천 답변 목록."""
    conv = get_conversation_or_404(db, conversation_id)
    history = build_history(conv)
    last_customer = next(
        (m["content"] for m in reversed(history) if m["role"] == "customer"), ""
    )
    past_cases = knowledge.retrieve(db, last_customer, limit=3)
    result = ai.recommend(history, past_cases=past_cases)
    return {
        "recommendations": result["recommendations"],
        "source": result["source"],
        "past_cases": past_cases,
    }


@router.post("/conversations/{conversation_id}/reply")
def agent_reply(conversation_id: int, payload: ReplyRequest, db: Session = Depends(get_db)):
    """상담원이 직접 답변을 전송한다."""
    conv = get_conversation_or_404(db, conversation_id)
    message = Message(conversation_id=conv.id, role="agent", content=payload.message.strip())
    db.add(message)
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True)


@router.post("/conversations/{conversation_id}/status")
def update_status(conversation_id: int, payload: StatusRequest, db: Session = Depends(get_db)):
    """상담 상태를 변경한다 (open / escalated / closed)."""
    if payload.status not in _STATUSES:
        raise HTTPException(status_code=400, detail="유효하지 않은 상태값입니다.")
    conv = get_conversation_or_404(db, conversation_id)
    conv.status = payload.status
    conv.updated_at = now()
    db.commit()
    # 상담 종료 시, 상담원이 답변한 내용을 학습 지식으로 저장
    if payload.status == "closed":
        knowledge.learn_from_conversation(db, conv)
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True)


@router.get("/knowledge")
def knowledge_stats(db: Session = Depends(get_db)):
    """학습된 상담 지식 통계: 항목 수와 검색 방식(임베딩 가능 여부)."""
    return {"count": knowledge.count(db), "embeddings": ai.embeddings_available()}


@router.get("/faq")
def get_faq():
    """FAQ 지식베이스 조회."""
    return faq.FAQS
