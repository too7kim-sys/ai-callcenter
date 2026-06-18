"""카테고리 사용량 분석 + 미분류 상담 발굴.

목적:
  • 매니저가 'AI 가 어떤 카테고리로 자주 분류하는가' 를 파악
  • '기타' / NULL 로 빠진 상담의 공통 키워드 → 새 카테고리 후보 추천

이 모듈은 분석 전용 — 카테고리 정의는 여전히 app/ai.py CATEGORY_KW 가 진실.
관리자는 결과를 보고 코드를 업데이트하거나, 향후 DB 기반으로 확장 가능.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from .. import auth, cache, faq
from ..database import get_db
from ..models import Conversation, Message
from ..permissions import P

router = APIRouter(prefix="/api/admin/categories", tags=["categories"])

_TTL = 60
# 키워드 추출용 — 한글/영문/숫자 2자 이상
_TOKEN_RE = re.compile(r"[A-Za-z가-힣0-9]{2,}")
# 분석에 도움이 안 되는 토큰
_STOPWORDS = {
    "안녕", "안녕하세요", "감사", "감사합니다", "고객", "상담", "문의", "확인",
    "있어요", "있습니다", "해주세요", "주세요", "있는", "있을", "있다", "없다",
    "그런데", "그리고", "그래서", "어떻게", "어떤", "어떻", "이런", "저런",
    "정말", "조금", "조금만", "다시", "다른", "오늘", "어제", "내일", "지금",
    "the", "and", "for", "you", "this", "that", "have", "with",
}


def _naive(dt):
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


@router.get("/usage")
def category_usage(
    days: int = 30,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """카테고리별 사용 빈도 + CSAT 평균 (최근 N일).

    days: 1~365 (기본 30)
    반환: [{name, count, csat_avg, csat_count, share}] 사용량 내림차순.
    """
    days = max(1, min(365, int(days)))
    return cache.get_or_compute(
        f"cat:usage:{days}",
        lambda: _compute_usage(db, days),
        _TTL,
    )


def _compute_usage(db: Session, days: int):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    convs = (
        db.query(Conversation.category, Conversation.customer_rating)
        .filter(Conversation.created_at >= since)
        .all()
    )
    by_cat: dict[str, dict] = {}
    for cat, rating in convs:
        key = cat or "(미분류)"
        b = by_cat.setdefault(key, {"count": 0, "ratings": []})
        b["count"] += 1
        if rating:
            b["ratings"].append(rating)

    total = sum(b["count"] for b in by_cat.values()) or 1
    rows = []
    for name, b in by_cat.items():
        ratings = b["ratings"]
        rows.append({
            "name": name,
            "count": b["count"],
            "share": round(b["count"] / total, 3),
            "csat_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "csat_count": len(ratings),
            "is_known": name in faq.CATEGORIES,
        })
    rows.sort(key=lambda r: r["count"], reverse=True)

    # 알려지지 않은(임시 분류 결과)·미분류만 별도 집계
    unknown = [r for r in rows if not r["is_known"] and r["name"] != "(미분류)"]
    missing = next((r for r in rows if r["name"] == "(미분류)"), None)
    return {
        "window_days": days,
        "total_conversations": total,
        "known_categories": len(faq.CATEGORIES),
        "missing": missing,
        "unknown_count": len(unknown),
        "rows": rows,
    }


@router.get("/uncategorized")
def uncategorized_suggestions(
    days: int = 30,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """미분류('기타' 또는 NULL) 상담의 공통 키워드 → 새 카테고리 후보.

    각 상담의 첫 고객 메시지 + 요약(있다면) 에서 토큰을 추출해 빈도 분석한다.
    기존 알려진 카테고리(faq.CATEGORIES)의 키워드와 겹치는 토큰은 제외 — 정말
    '새로운' 패턴만 노출.
    """
    days = max(1, min(365, int(days)))
    limit = max(5, min(100, int(limit)))
    return cache.get_or_compute(
        f"cat:uncat:{days}:{limit}",
        lambda: _compute_uncategorized(db, days, limit),
        _TTL,
    )


def _compute_uncategorized(db: Session, days: int, limit: int):
    from .. import ai

    since = datetime.now(timezone.utc) - timedelta(days=days)
    convs = (
        db.query(Conversation)
        .options(selectinload(Conversation.messages))
        .filter(
            Conversation.created_at >= since,
            (Conversation.category.is_(None)) | (Conversation.category == "기타"),
        )
        .order_by(Conversation.created_at.desc())
        .all()
    )

    # 기존 카테고리의 키워드 집합 — 추천에서 제외
    known_kw: set[str] = set()
    for kws in ai.CATEGORY_KW.values():
        for k in kws:
            known_kw.add(k.lower())

    token_counts: Counter[str] = Counter()
    samples: list[dict] = []
    for c in convs:
        first_msg = next((m for m in c.messages if m.role == "customer"), None)
        text_parts = [c.summary or "", first_msg.content if first_msg else ""]
        text = " ".join(text_parts).lower()
        for tok in _TOKEN_RE.findall(text):
            if len(tok) < 2 or tok in _STOPWORDS or tok in known_kw:
                continue
            token_counts[tok] += 1
        if len(samples) < 30 and first_msg:
            samples.append({
                "conversation_id": c.id,
                "snippet": first_msg.content[:120],
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })

    top = [{"token": t, "count": n} for t, n in token_counts.most_common(limit)]
    return {
        "window_days": days,
        "uncategorized_count": len(convs),
        "suggested_keywords": top,
        "samples": samples,
    }
