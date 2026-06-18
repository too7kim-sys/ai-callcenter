"""대시보드 API — 오늘 한눈에 보기 + 심화 KPI."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone

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


def _naive(dt):
    """SQLite 저장값(naive) 과 anyaware 값을 모두 안전하게 비교/연산."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _seconds_between(start, end):
    start, end = _naive(start), _naive(end)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return delta if delta >= 0 else None


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


@router.get("/dashboard/kpi")
def dashboard_kpi(
    days: int = 7,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """심화 KPI: AHT(평균 처리 시간) · FRT(평균 첫응답 시간) · FCR(1차 해결률) · 일별 추이.

    days 는 1~90 사이로 클램프. 종료된 상담만 AHT/FRT 평균에 들어간다.
    """
    days = max(1, min(90, int(days)))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    convs_in_window = (
        db.query(Conversation).filter(Conversation.created_at >= since).all()
    )
    closed = [c for c in convs_in_window if c.status == "closed"]

    aht_values = [
        s for s in (_seconds_between(c.created_at, c.updated_at) for c in closed)
        if s is not None and s > 0
    ]
    aht_avg = round(sum(aht_values) / len(aht_values)) if aht_values else None

    frt_values: list[float] = []
    for c in closed:
        first_cust = None
        first_resp = None
        for m in c.messages:  # 이미 id 기준 정렬됨
            if m.role == "customer" and first_cust is None:
                first_cust = m.created_at
            elif m.role in ("ai", "agent") and first_cust is not None:
                first_resp = m.created_at
                break
        s = _seconds_between(first_cust, first_resp)
        if s is not None:
            frt_values.append(s)
    frt_avg = round(sum(frt_values) / len(frt_values)) if frt_values else None

    # FCR: 종료 상담 중 (에스컬레이션·고객 호출·고위험 신호) 없이 닫힌 비율
    if closed:
        fcr_ok = sum(
            1 for c in closed
            if c.risk_level != "high" and not c.agent_requested
        )
        fcr_rate = round(fcr_ok / len(closed), 3)
    else:
        fcr_rate = None

    # 에스컬레이션율 (창 안 전체 상담 중)
    if convs_in_window:
        esc = sum(
            1 for c in convs_in_window
            if c.status == "escalated" or c.risk_level == "high" or c.agent_requested
        )
        esc_rate = round(esc / len(convs_in_window), 3)
    else:
        esc_rate = None

    # 일별 추이
    trend = []
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days - 1, -1, -1):
        day_start = today - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_start_n = _naive(day_start)
        day_end_n = _naive(day_end)
        day_convs = [
            c for c in convs_in_window
            if day_start_n <= _naive(c.created_at) < day_end_n
        ]
        day_closed = [c for c in day_convs if c.status == "closed"]
        day_aht = [
            s for s in (_seconds_between(c.created_at, c.updated_at) for c in day_closed)
            if s is not None and s > 0
        ]
        trend.append({
            "date": day_start.date().isoformat(),
            "conversations": len(day_convs),
            "closed": len(day_closed),
            "aht_seconds": round(sum(day_aht) / len(day_aht)) if day_aht else 0,
        })

    return {
        "window_days": days,
        "sample_size_closed": len(closed),
        "sample_size_total": len(convs_in_window),
        "aht_seconds": aht_avg,
        "frt_seconds": frt_avg,
        "fcr_rate": fcr_rate,
        "escalation_rate": esc_rate,
        "trend": trend,
    }
