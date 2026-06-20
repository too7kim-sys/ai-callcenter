"""의심 활동 감지 (anomaly detection).

추적 항목:
  1) failed_login   — 같은 IP 의 로그인/2FA 실패 폭주
                      → 5분에 5회 이상이면 '무차별 대입 의심' 알림
  2) multi_ip_login — 같은 사용자 계정의 다중 IP 로그인
                      → 30분에 3개 IP 이상이면 '계정 도용 가능성' 알림

설계:
  • 인메모리 슬라이딩 윈도우 — 단일 프로세스 가정 (멀티 워커는 Redis 필요).
  • 알림 폭주 차단을 위해 키 별 10분 쿨다운.
  • 감지 시 Slack 알림(notifier) + 감사 로그(audit) 동시 기록.
  • 표준 라이브러리만 사용.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from . import audit, notifier

logger = logging.getLogger("ai_callcenter.anomaly")

# ----- 실패 로그인 (per IP) -----
FAILED_WINDOW_SECS = 300     # 5분
FAILED_THRESHOLD = 5
_failed: dict[str, deque[float]] = defaultdict(deque)
_failed_lock = threading.Lock()

# ----- 다중 IP 로그인 (per user) -----
MULTI_IP_WINDOW_SECS = 1800  # 30분
MULTI_IP_THRESHOLD = 3
_user_ips: dict[int, dict[str, float]] = defaultdict(dict)
_user_lock = threading.Lock()

# 키 별 마지막 알림 시각 — 쿨다운
ALERT_COOLDOWN_SECS = 600
_alert_cooldown: dict[str, float] = {}
_cooldown_lock = threading.Lock()


def on_failed_login(db, ip: str | None, username: str, *, stage: str = "password") -> int:
    """로그인 또는 2FA 실패 시 호출. 윈도우 내 실패 횟수를 반환."""
    if not ip:
        ip = "unknown"
    now = time.monotonic()
    cutoff = now - FAILED_WINDOW_SECS
    with _failed_lock:
        bucket = _failed[ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        count = len(bucket)
    if count >= FAILED_THRESHOLD and _try_cooldown(f"failed:{ip}"):
        _alert_failed(db, ip, count, username, stage)
    _maybe_gc(now)
    return count


def on_login_success(db, user, ip: str | None) -> int:
    """로그인 성공 시 호출. 윈도우 내 distinct IP 수를 반환."""
    if not ip or user is None:
        return 0
    now = time.monotonic()
    cutoff = now - MULTI_IP_WINDOW_SECS
    with _user_lock:
        ips = _user_ips[user.id]
        for k in [k for k, t in ips.items() if t < cutoff]:
            ips.pop(k, None)
        ips[ip] = now
        distinct = len(ips)
    # 실패 카운터는 성공 시 같은 IP 에 한해 초기화 — 사용자 본인 정상 로그인
    with _failed_lock:
        _failed.pop(ip, None)
    if distinct >= MULTI_IP_THRESHOLD and _try_cooldown(f"multi:{user.id}"):
        _alert_multi_ip(db, user, list(ips.keys()))
    _maybe_gc(now)
    return distinct


# --------------------------------------------------------------------
# 메모리 누수 방지 — 빈 버킷 / 만료된 쿨다운 / 비활성 사용자 IP 주기적 정리.
# 10분에 1회 lazy 청소 (호출자 스레드에서 마지막 청소 후 600초 경과 시).
# --------------------------------------------------------------------
_GC_INTERVAL_SECS = 600
_last_gc_at = 0.0
_gc_lock = threading.Lock()


def _maybe_gc(now: float) -> None:
    global _last_gc_at
    with _gc_lock:
        if now - _last_gc_at < _GC_INTERVAL_SECS:
            return
        _last_gc_at = now

    failed_cutoff = now - FAILED_WINDOW_SECS
    with _failed_lock:
        empty_ips: list[str] = []
        for ip, bucket in _failed.items():
            while bucket and bucket[0] < failed_cutoff:
                bucket.popleft()
            if not bucket:
                empty_ips.append(ip)
        for ip in empty_ips:
            _failed.pop(ip, None)

    user_cutoff = now - MULTI_IP_WINDOW_SECS
    with _user_lock:
        empty_users: list[int] = []
        for uid, ips in _user_ips.items():
            for k in [k for k, t in ips.items() if t < user_cutoff]:
                ips.pop(k, None)
            if not ips:
                empty_users.append(uid)
        for uid in empty_users:
            _user_ips.pop(uid, None)

    cooldown_cutoff = now - 2 * ALERT_COOLDOWN_SECS
    with _cooldown_lock:
        expired = [k for k, t in _alert_cooldown.items() if t < cooldown_cutoff]
        for k in expired:
            _alert_cooldown.pop(k, None)


def _try_cooldown(key: str) -> bool:
    """알림 폭주 차단 — 같은 키에 대해 쿨다운 안이면 False."""
    now = time.monotonic()
    with _cooldown_lock:
        last = _alert_cooldown.get(key, 0)
        if now - last < ALERT_COOLDOWN_SECS:
            return False
        _alert_cooldown[key] = now
    return True


def _alert_failed(db, ip: str, count: int, username: str, stage: str):
    safe_user = (username or "(빈값)")[:50]
    logger.warning("anomaly: failed-login spike ip=%s count=%d", ip, count)
    try:
        audit.log(db, None, "anomaly.failed_login_spike",
                  target_type="ip", target_id=ip,
                  details={"count": count, "window_sec": FAILED_WINDOW_SECS,
                           "last_username": safe_user, "stage": stage})
    except Exception:
        pass
    notifier.notify(
        title="로그인 실패 폭주 감지",
        body=(
            f"IP `{ip}` 에서 {FAILED_WINDOW_SECS//60}분간 *{count}회* 실패.\n"
            f"마지막 시도 사용자: `{safe_user}` (단계: {stage})\n"
            f"무차별 대입 또는 자동화 공격 의심."
        ),
        severity="warning",
    )


def _alert_multi_ip(db, user, ips: list[str]):
    logger.warning("anomaly: multi-IP login user=%s ips=%s", user.username, ips)
    try:
        audit.log(db, user, "anomaly.multi_ip_login",
                  target_type="user", target_id=user.id,
                  details={"distinct_ips": ips, "window_sec": MULTI_IP_WINDOW_SECS})
    except Exception:
        pass
    notifier.notify(
        title="동일 계정 다중 IP 로그인",
        body=(
            f"사용자 `{user.username}` 가 {MULTI_IP_WINDOW_SECS//60}분 안에 "
            f"*{len(ips)}개 IP* 에서 로그인 성공.\n"
            f"계정 도용 가능성 점검 필요.\n"
            f"IPs: " + ", ".join(f"`{i}`" for i in ips[:5])
        ),
        severity="warning",
    )


# --------------------------------------------------------------------
# 운영 도우미
# --------------------------------------------------------------------

def status() -> dict:
    """현재 추적 상태 — 관리자 디버그용."""
    with _failed_lock:
        failed_now = {ip: len(b) for ip, b in _failed.items() if b}
    with _user_lock:
        users_now = {uid: list(ips.keys()) for uid, ips in _user_ips.items() if ips}
    return {
        "failed_login_by_ip": failed_now,
        "user_login_ips": users_now,
        "thresholds": {
            "failed": {"window_sec": FAILED_WINDOW_SECS, "limit": FAILED_THRESHOLD},
            "multi_ip": {"window_sec": MULTI_IP_WINDOW_SECS, "limit": MULTI_IP_THRESHOLD},
            "alert_cooldown_sec": ALERT_COOLDOWN_SECS,
        },
    }


def reset_for_tests():
    with _failed_lock:
        _failed.clear()
    with _user_lock:
        _user_ips.clear()
    with _cooldown_lock:
        _alert_cooldown.clear()
