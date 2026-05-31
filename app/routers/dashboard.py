"""대시보드 API — 오늘 한눈에 보기."""
from collections import Counter
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import AgentUser, Conversation, KnowledgeItem, Message
from ..permissions import P

router = APIRouter(prefix="/api", tags=["dashboard"])


def _today_start():
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """오늘 한눈에 — 카운트·분포·상위 카테고리·내 담당."""
    today = _today_start()

    total_today = db.query(func.count(Conversation.id)).filter(Conversation.created_at >= today).scalar() or 0
    total_all = db.query(func.count(Conversation.id)).scalar() or 0

    # 상태별
    rows = db.query(Conversation.status, func.count(Conversation.id)).group_by(Conversation.status).all()
    by_status = {s: n for s, n in rows}

    # 감정 분포 (전체)
    sentiment_rows = db.query(Conversation.sentiment, func.count(Conversation.id)).group_by(Conversation.sentiment).all()
    by_sentiment = {(s or "(미평가)"): n for s, n in sentiment_rows}

    # 위험도 분포 (전체)
    risk_rows = db.query(Conversation.risk_level, func.count(Conversation.id)).group_by(Conversation.risk_level).all()
    by_risk = {(r or "low"): n for r, n in risk_rows}

    # 카테고리 상위 5
    cat_rows = (
        db.query(Conversation.category, func.count(Conversation.id))
        .filter(Conversation.category.isnot(None))
        .group_by(Conversation.category)
        .order_by(func.count(Conversation.id).desc())
        .limit(5)
        .all()
    )
    top_categories = [{"name": c, "count": n} for c, n in cat_rows]

    # 내 담당 / 미배정
    mine = db.query(func.count(Conversation.id)).filter(Conversation.assigned_agent_id == user.id).scalar() or 0
    unassigned = db.query(func.count(Conversation.id)).filter(
        Conversation.assigned_agent_id.is_(None),
        Conversation.status != "closed",
    ).scalar() or 0

    # 최근 24h 메시지 수
    msg_today = db.query(func.count(Message.id)).filter(Message.created_at >= today).scalar() or 0

    # 학습 항목
    learned = db.query(func.count(KnowledgeItem.id)).scalar() or 0

    # 평균 만족도 (1~5)
    rating_rows = db.query(Conversation.customer_rating).filter(Conversation.customer_rating.isnot(None)).all()
    ratings = [r[0] for r in rating_rows if r[0]]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    # 활성 사용자 수
    active_users = db.query(func.count(AgentUser.id)).filter(AgentUser.active == True).scalar() or 0  # noqa: E712

    return {
        "today": {
            "new_conversations": total_today,
            "messages": msg_today,
        },
        "totals": {
            "conversations": total_all,
            "learned_items": learned,
            "active_users": active_users,
        },
        "by_status": by_status,
        "by_sentiment": by_sentiment,
        "by_risk": by_risk,
        "top_categories": top_categories,
        "me": {
            "assigned": mine,
            "unassigned_queue": unassigned,
            "username": user.username,
        },
        "satisfaction": {"avg": avg_rating, "count": len(ratings)},
    }
