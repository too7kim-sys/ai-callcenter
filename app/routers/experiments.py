"""A/B 프롬프트 실험 분석.

  GET /api/admin/experiments/ai_variant?days=N
    변형(A/B) 별: 상담 수, CSAT 평균, 감정 분포, AI 답변 평균 길이, 에스컬레이션율

  결과 해석:
    • CSAT 평균이 통계적으로 유의미하게 높은 변형이 'winning'
    • 표본이 충분(각 변형 30건+)할 때 의사결정에 사용 권장
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, selectinload

from .. import auth, cache, config
from ..database import get_db
from ..models import Conversation, Message
from ..permissions import P

router = APIRouter(prefix="/api/admin/experiments", tags=["experiments"])


@router.get("/ai_variant")
def ai_variant_comparison(
    days: int = 30,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """A/B 프롬프트 변형 비교 (1~365일)."""
    days = max(1, min(365, int(days)))
    return cache.get_or_compute(
        f"exp:ai_variant:{days}",
        lambda: _compute(db, days),
        60,
    )


def _compute(db: Session, days: int):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    convs = (
        db.query(Conversation)
        .options(selectinload(Conversation.messages))
        .filter(
            Conversation.created_at >= since,
            Conversation.ai_variant.isnot(None),
        )
        .all()
    )

    groups: dict[str, dict] = {"A": _new_bucket(), "B": _new_bucket()}
    for c in convs:
        v = c.ai_variant
        if v not in groups:
            continue
        b = groups[v]
        b["count"] += 1
        if c.customer_rating:
            b["ratings"].append(c.customer_rating)
        if c.status == "escalated" or c.risk_level == "high" or c.agent_requested:
            b["escalated"] += 1
        s = c.sentiment
        if s:
            b["sentiment"][s] = b["sentiment"].get(s, 0) + 1
        # AI 답변 평균 길이
        ai_msgs = [m.content for m in c.messages if m.role == "ai" and m.content]
        if ai_msgs:
            b["ai_msg_lens"].extend(len(t) for t in ai_msgs)

    summary = {v: _summarize_bucket(b) for v, b in groups.items()}

    # 위너 판정 — 표본 30건 이상 필요, CSAT 차이 ≥0.2 점이면 의미 있음
    winner = None
    sample_min = 30
    a, b = summary["A"], summary["B"]
    if a["count"] >= sample_min and b["count"] >= sample_min:
        if a["csat_avg"] is not None and b["csat_avg"] is not None:
            diff = b["csat_avg"] - a["csat_avg"]
            if abs(diff) >= 0.2:
                winner = "B" if diff > 0 else "A"

    return {
        "window_days": days,
        "enabled": config.AB_EXPERIMENT_ENABLED,
        "ratio_b": config.AB_EXPERIMENT_RATIO_B,
        "variants": summary,
        "winner": winner,
        "min_sample_for_decision": sample_min,
    }


def _new_bucket():
    return {
        "count": 0, "ratings": [], "escalated": 0,
        "sentiment": {}, "ai_msg_lens": [],
    }


def _summarize_bucket(b: dict) -> dict:
    cnt = b["count"]
    ratings = b["ratings"]
    lens = b["ai_msg_lens"]
    return {
        "count": cnt,
        "csat_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "csat_count": len(ratings),
        "escalation_rate": round(b["escalated"] / cnt, 3) if cnt else 0,
        "avg_ai_reply_length": round(sum(lens) / len(lens), 1) if lens else None,
        "sentiment_distribution": b["sentiment"],
    }
