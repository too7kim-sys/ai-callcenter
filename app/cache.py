"""인메모리 TTL 캐시.

용도:
  • 대시보드 집계처럼 비싼 계산을 짧은 TTL 로 공유 — 매니저가 새로고침
    연타해도 1회만 DB 히트.
  • 단일 프로세스 가정. 멀티 워커 환경에선 Redis 등으로 교체.

설계:
  • 스레드 안전 (단일 락).
  • 매 조회 시 만료 항목 lazily 정리. 별도 GC 스레드 없음.
  • get_or_compute() 헬퍼로 read-through 패턴을 한 줄로.
  • 통계(stats) + 무효화(invalidate_prefix) 노출 — 운영 디버그/수동 클리어.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("ai_callcenter.cache")


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value, expires_at: float):
        self.value = value
        self.expires_at = expires_at


_store: dict[str, _Entry] = {}
_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "writes": 0}


def get(key: str):
    """캐시 조회. 만료/없으면 None."""
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            _stats["misses"] += 1
            return None
        if now > entry.expires_at:
            _store.pop(key, None)
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        return entry.value


def set(key: str, value, ttl_seconds: float):
    expires = time.monotonic() + ttl_seconds
    with _lock:
        _store[key] = _Entry(value, expires)
        _stats["writes"] += 1


def get_or_compute(key: str, compute: Callable, ttl_seconds: float):
    """read-through: 캐시 미스면 compute() 실행 후 저장."""
    hit = get(key)
    if hit is not None:
        return hit
    value = compute()
    set(key, value, ttl_seconds)
    return value


def invalidate(key: str):
    with _lock:
        _store.pop(key, None)


def invalidate_prefix(prefix: str) -> int:
    """prefix 로 시작하는 키 일괄 삭제. 삭제 개수 반환."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            _store.pop(k, None)
    return len(keys)


def clear_all() -> int:
    with _lock:
        n = len(_store)
        _store.clear()
    return n


def stats() -> dict:
    now = time.monotonic()
    with _lock:
        active = sum(1 for e in _store.values() if e.expires_at > now)
        total = len(_store)
        hits = _stats["hits"]
        misses = _stats["misses"]
    hit_rate = round(hits / (hits + misses), 3) if (hits + misses) else 0.0
    return {
        "size_active": active,
        "size_total": total,
        "hits": hits,
        "misses": misses,
        "writes": _stats["writes"],
        "hit_rate": hit_rate,
    }


def reset_for_tests():
    with _lock:
        _store.clear()
        for k in _stats:
            _stats[k] = 0
