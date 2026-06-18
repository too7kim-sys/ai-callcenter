"""엔드포인트별 응답 시간 메트릭.

설계:
  • 미들웨어가 모든 요청의 응답 시간(ms)을 path/method/status 별로 수집.
  • 경로의 동적 부분(/:id) 은 정규화해 cardinality 폭주 차단.
  • 최근 1000개 표본 슬라이딩 윈도우 + 카운트 누적.
  • p50/p95/p99 계산은 stats() 호출 시 1회만.
  • 단일 프로세스 가정. Prometheus 등 외부 시스템 연동은 별도 출력 포맷으로.

저장:
  _samples[key]  — deque(maxlen=1000) — 최근 ms 표본
  _counts[key]   — {total, 4xx, 5xx} 누적
  key = "METHOD /path/{id}"
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict, deque

_MAX_SAMPLES = 1000

_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES))
_counts: dict[str, dict] = defaultdict(lambda: {"total": 0, "4xx": 0, "5xx": 0})
_lock = threading.Lock()

# 동적 path segment 정규화 — /api/conversations/42 → /api/conversations/{id}
_DIGIT_SEG = re.compile(r"/\d+(?=/|$)")
_LEARNED_ID = re.compile(r"/L\d+(?=/|$)")


def record(method: str, path: str, status: int, latency_ms: float) -> None:
    key = f"{method} {_normalize(path)}"
    with _lock:
        _samples[key].append(latency_ms)
        c = _counts[key]
        c["total"] += 1
        if 500 <= status < 600:
            c["5xx"] += 1
        elif 400 <= status < 500:
            c["4xx"] += 1


def stats(limit: int = 50) -> list[dict]:
    """엔드포인트별 p50/p95/p99 + 에러율. 요청 수 내림차순."""
    rows = []
    with _lock:
        items = [(k, list(v)) for k, v in _samples.items()]
        counts_snapshot = {k: dict(v) for k, v in _counts.items()}
    for key, raw in items:
        if not raw:
            continue
        s = sorted(raw)
        n = len(s)
        c = counts_snapshot.get(key, {"total": n, "4xx": 0, "5xx": 0})
        rows.append({
            "endpoint": key,
            "count": c["total"],
            "samples": n,
            "p50_ms": round(s[int(n * 0.50)], 1),
            "p95_ms": round(s[min(int(n * 0.95), n - 1)], 1),
            "p99_ms": round(s[min(int(n * 0.99), n - 1)], 1),
            "avg_ms": round(sum(s) / n, 1),
            "error_4xx": c["4xx"],
            "error_5xx": c["5xx"],
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows[:limit]


def summary() -> dict:
    """전체 요약 — 총 요청 수, 평균 응답시간."""
    with _lock:
        all_samples = []
        total = 0
        err4 = 0
        err5 = 0
        for k, v in _samples.items():
            all_samples.extend(v)
            c = _counts[k]
            total += c["total"]
            err4 += c["4xx"]
            err5 += c["5xx"]
    if not all_samples:
        return {"requests": total, "avg_ms": 0, "endpoints": len(_samples)}
    return {
        "requests": total,
        "endpoints": len(_samples),
        "avg_ms": round(sum(all_samples) / len(all_samples), 1),
        "error_4xx": err4,
        "error_5xx": err5,
        "error_rate_4xx": round(err4 / total, 4) if total else 0,
        "error_rate_5xx": round(err5 / total, 4) if total else 0,
    }


def reset_for_tests():
    with _lock:
        _samples.clear()
        _counts.clear()


def _normalize(path: str) -> str:
    p = _LEARNED_ID.sub("/{id}", path)
    p = _DIGIT_SEG.sub("/{id}", p)
    return p
