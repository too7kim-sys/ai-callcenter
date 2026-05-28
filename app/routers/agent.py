"""상담원 콘솔 API: 상담 목록, AI 요약·분류, 답변 추천, 답변 전송, 상태 변경."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ai, faq, knowledge
from ..database import get_db
from ..models import Conversation, KnowledgeItem, Message
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


@router.get("/knowledge/items")
def list_knowledge_items(db: Session = Depends(get_db)):
    """학습된 지식 전체 목록 (관리·검수용).

    FAQ에 노출되지 않는 짧은 답변 항목도 모두 포함하며, 출처 상담의
    고객명·상태와 임베딩 보유 여부, FAQ 노출 여부를 함께 반환한다.
    """
    items = db.query(KnowledgeItem).order_by(KnowledgeItem.id.desc()).all()
    result = []
    for row in items:
        conv = db.get(Conversation, row.conversation_id)
        answer = row.answer or ""
        result.append({
            "id": row.id,
            "conversation_id": row.conversation_id,
            "customer_name": conv.customer_name if conv else None,
            "conversation_status": conv.status if conv else None,
            "question": row.question,
            "answer": answer,
            "answer_length": len(answer.strip()),
            "faq_visible": len(answer.strip()) >= _MIN_LEARNED_ANSWER_LEN,
            "has_embedding": bool(row.embedding),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return result


@router.delete("/knowledge/items/{item_id}")
def delete_knowledge_item(item_id: int, db: Session = Depends(get_db)):
    """잘못 학습된 항목을 삭제한다 (검수용)."""
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="학습 항목을 찾을 수 없습니다.")
    db.delete(item)
    db.commit()
    return {"ok": True, "deleted_id": item_id}


_MIN_LEARNED_ANSWER_LEN = 20  # 너무 짧은 상담원 답변은 FAQ로 노출하지 않음


@router.get("/faq")
def get_faq(db: Session = Depends(get_db)):
    """FAQ 지식베이스 조회 — 정적 FAQ + 종료된 상담에서 학습된 항목을 함께 반환한다.

    각 항목에 `source`("curated"|"learned") 가 포함되며, 학습 항목은
    출처 상담 ID(`conversation_id`)와 학습 시각(`learned_at`) 을 함께 노출한다.
    """
    items = [{**entry, "source": "curated"} for entry in faq.FAQS]

    learned_rows = db.query(KnowledgeItem).order_by(KnowledgeItem.id).all()
    for row in learned_rows:
        if not row.answer or len(row.answer.strip()) < _MIN_LEARNED_ANSWER_LEN:
            continue  # 너무 짧은 답변은 FAQ 노출 제외
        conv = db.get(Conversation, row.conversation_id)
        category = (conv.category if conv and conv.category
                    else ai.classify_category(row.question))
        items.append({
            "id": f"L{row.id}",
            "category": category,
            "question": row.question,
            "answer": row.answer,
            "keywords": [],
            "source": "learned",
            "conversation_id": row.conversation_id,
            "learned_at": row.created_at.isoformat() if row.created_at else None,
        })
    return items
