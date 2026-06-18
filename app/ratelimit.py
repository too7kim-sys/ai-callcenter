"""IP 기반 인메모리 슬라이딩 윈도우 레이트 리미터.

설계 원칙:
  • 외부 의존 X — stdlib (deque + threading.Lock) 만 사용.
  • 단일 프로세스 가정. 멀티 워커·리플리카 환경에선 Redis 등 외부 저장소
    기반 리미터(예: slowapi + redis)로 교체 필요.
  • 익명 엔드포인트 (고객 채팅 입구, 로그인, TTS) 만 적용 — 인증된 상담원
    엔드포인트는 권한 검사로 충분히 보호됨.

사용 예 (FastAPI 의존성):
    @router.post("/foo", dependencies=[Depends(rate_limit("foo", 30, 60))])
    def foo(...): ...

리미트 초과 시 HTTP 429 + Retry-After 헤더 반환.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request

logger = logging.getLogger("ai_callcenter.ratelimit")

# 글로벌 enable/disable 스위치 (테스트 / 개발 편의)
ENABLED = (os.getenv("RATELIMIT_ENABLED", "true").strip().lower() != "false")

# (한도, 윈도우초) 별 버킷 저장소
_buckets: dict[tuple[str, str], deque[float]] = {}
_buckets_lock = Lock()

# 청소 (만료 키 제거) 주기 — 마지막 청소 이후 이 초가 지나면 1회 실행
_CLEANUP_INTERVAL = 600
_last_cleanup = 0.0


def client_ip(request: Request) -> str:
    """클라이언트 IP. 신뢰할 수 있는 프록시 뒤에 있는 경우 X-Forwarded-For 첫번째 값.

    프록시가 헤더를 위변조 가능한 환경(직접 노출)에서는 첫 hop 만 신뢰.
    """
    fwd = request.headers.get("x-forwarded-for", "").strip()
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def rate_limit(name: str, max_requests: int, window_seconds: int = 60):
    """FastAPI 의존성 팩토리.

    name 은 엔드포인트 식별자 (서로 다른 엔드포인트가 버킷을 공유하지 않도록).
    """

    def dependency(request: Request):
        if not ENABLED:
            return
        ip = client_ip(request)
        key = (name, ip)
        ok, wait_secs = _check(key, max_requests, window_seconds)
        if not ok:
            logger.info("rate-limited: %s ip=%s wait=%ss", name, ip, wait_secs)
            raise HTTPException(
                status_code=429,
                detail=f"요청이 너무 많습니다. {wait_secs}초 후 다시 시도해 주세요.",
                headers={"Retry-After": str(wait_secs)},
            )

    return dependency


# --------------------------------------------------------------------

def _check(key: tuple[str, str], max_requests: int, window_seconds: int):
    """슬라이딩 윈도우 검사. 반환: (allowed, retry_after_secs)."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _buckets_lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = deque()
            _buckets[key] = bucket
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            retry_after = max(1, int(bucket[0] + window_seconds - now) + 1)
            return False, retry_after
        bucket.append(now)
    _maybe_cleanup(now)
    return True, 0


def _maybe_cleanup(now: float):
    """오래된 빈 버킷 제거 (메모리 누수 방지)."""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    with _buckets_lock:
        _last_cleanup = now
        empty = [k for k, b in _buckets.items() if not b]
        for k in empty:
            del _buckets[k]


def reset_for_tests():
    """테스트용 — 모든 버킷 초기화."""
    with _buckets_lock:
        _buckets.clear()
