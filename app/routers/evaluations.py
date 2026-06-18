"""AI 답변 품질 자동 평가 (LLM-as-judge).

엔드포인트:
  POST /api/admin/evaluations/run?sample=N&days=K
    최근 K일 안에 평가되지 않은 AI 답변 중 무작위 N건 평가 트리거.
    Claude/Ollama 가용 시 실제 LLM, 아니면 mock 휴리스틱.

  GET /api/admin/evaluations/summary?days=N
    최근 N일 평가 점수 평균 + 변형(A/B) 별 비교 + 낮은 점수 답변 샘플.

  GET /api/admin/evaluations/recent?limit=K
    최근 평가 K건 (drill-down 용).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session, selectinload

from .. import ai, auth, cache
from ..database import SessionLocal, get_db
from ..models import Conversation, Message, MessageEvaluation
from ..permissions import P

router = APIRouter(prefix="/api/admin/evaluations", tags=["evaluations"])


@router.post("/run")
def trigger_evaluations(
    background: BackgroundTasks,
    sample: int = 20,
    days: int = 7,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """평가되지 않은 AI 답변 중 무작위 N건을 백그라운드에서 채점."""
    sample = max(1, min(200, int(sample)))
    days = max(1, min(90, int(days)))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 이미 평가된 message_id 제외
    evaluated = {
        row[0] for row in
        db.query(MessageEvaluation.message_id)
          .filter(MessageEvaluation.created_at >= since).all()
    }
    # 평가 후보 — 최근 N일의 AI 메시지
    candidate_ids = [
        row[0] for row in
        db.query(Message.id)
          .filter(Message.role == "ai", Message.created_at >= since).all()
        if row[0] not in evaluated
    ]
    random.shuffle(candidate_ids)
    target_ids = candidate_ids[:sample]

    background.add_task(_run_batch, target_ids)
    # 캐시 무효화 — 다음 summary 조회는 새 결과 반영
    cache.invalidate_prefix("eval:")
    return {
        "queued": len(target_ids),
        "candidates_remaining": max(0, len(candidate_ids) - sample),
        "evaluated_in_window": len(evaluated),
    }


def _run_batch(message_ids: list[int]):
    """백그라운드 — 자체 DB 세션. 평가 결과 저장."""
    if not message_ids:
        return
    db = SessionLocal()
    try:
        msgs = (
            db.query(Message)
              .filter(Message.id.in_(message_ids))
              .all()
        )
        for ai_msg in msgs:
            # 같은 상담의 직전 고객 메시지 찾기
            customer = (
                db.query(Message)
                  .filter(
                      Message.conversation_id == ai_msg.conversation_id,
                      Message.role == "customer",
                      Message.id < ai_msg.id,
                  )
                  .order_by(Message.id.desc())
                  .first()
            )
            if not customer:
                continue
            scores = ai.evaluate_reply(customer.content, ai_msg.content)
            conv = db.get(Conversation, ai_msg.conversation_id)
            db.add(MessageEvaluation(
                message_id=ai_msg.id,
                conversation_id=ai_msg.conversation_id,
                helpfulness=scores["helpfulness"],
                accuracy=scores["accuracy"],
                tone=scores["tone"],
                reasoning=scores["reasoning"],
                variant=conv.ai_variant if conv else None,
                source=scores["source"],
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/summary")
def evaluations_summary(
    days: int = 30,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    days = max(1, min(365, int(days)))
    return cache.get_or_compute(
        f"eval:summary:{days}",
        lambda: _compute_summary(db, days),
        60,
    )


def _compute_summary(db: Session, days: int):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    evals = (
        db.query(MessageEvaluation)
          .filter(MessageEvaluation.created_at >= since)
          .all()
    )
    if not evals:
        return {"window_days": days, "count": 0, "avg": None, "by_variant": {}, "low_scores": []}

    def avg(xs): return round(sum(xs) / len(xs), 2) if xs else None

    overall_h = [e.helpfulness for e in evals]
    overall_a = [e.accuracy for e in evals]
    overall_t = [e.tone for e in evals]

    by_variant: dict[str, dict] = {}
    for v in ("A", "B"):
        bucket = [e for e in evals if e.variant == v]
        if bucket:
            by_variant[v] = {
                "count": len(bucket),
                "helpfulness": avg([e.helpfulness for e in bucket]),
                "accuracy":    avg([e.accuracy    for e in bucket]),
                "tone":        avg([e.tone        for e in bucket]),
                "total":       avg([(e.helpfulness + e.accuracy + e.tone) / 3 for e in bucket]),
            }

    # 낮은 점수 답변 샘플 (3개 지표 합 ≤ 6)
    low = [e for e in evals if (e.helpfulness + e.accuracy + e.tone) <= 6]
    low.sort(key=lambda e: e.helpfulness + e.accuracy + e.tone)
    low_samples = []
    for e in low[:10]:
        m = db.get(Message, e.message_id)
        low_samples.append({
            "conversation_id": e.conversation_id,
            "message_id": e.message_id,
            "scores": {"h": e.helpfulness, "a": e.accuracy, "t": e.tone},
            "reasoning": e.reasoning,
            "ai_reply": (m.content if m else "")[:200],
        })

    return {
        "window_days": days,
        "count": len(evals),
        "avg": {
            "helpfulness": avg(overall_h),
            "accuracy":    avg(overall_a),
            "tone":        avg(overall_t),
            "total":       avg([(h + a + t) / 3 for h, a, t in zip(overall_h, overall_a, overall_t)]),
        },
        "by_variant": by_variant,
        "low_scores": low_samples,
    }


@router.get("/recent")
def evaluations_recent(
    limit: int = 50,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    limit = max(1, min(500, int(limit)))
    rows = (
        db.query(MessageEvaluation)
          .order_by(MessageEvaluation.id.desc())
          .limit(limit)
          .all()
    )
    return [{
        "id": e.id,
        "conversation_id": e.conversation_id,
        "message_id": e.message_id,
        "helpfulness": e.helpfulness,
        "accuracy": e.accuracy,
        "tone": e.tone,
        "total": round((e.helpfulness + e.accuracy + e.tone) / 3, 2),
        "reasoning": e.reasoning,
        "variant": e.variant,
        "source": e.source,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]
