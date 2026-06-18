"""표준 라이브러리만 사용 — 빠른 부하 테스트.

외부 의존(locust) 없이 동시 클라이언트로 일정 횟수 요청 후 응답 시간
분포 출력. 운영 중인 서버에 대해 가볍게 회귀 확인용.

사용법:
    python scripts/loadtest_quick.py --url http://localhost:8000 \\
        --workers 10 --requests 200

기본 시나리오: 고객 채팅 흐름 (create → chat × N → end).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

MESSAGES = [
    "안녕하세요",
    "환불 처리 어떻게 하나요?",
    "주문번호 12345 상태 확인 부탁드립니다",
    "결제 취소 요청합니다",
    "감사합니다",
]


def _post(url: str, body: dict, timeout: float = 10) -> tuple[int, dict, float]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
            return r.status, body, (time.perf_counter() - t) * 1000
    except urllib.error.HTTPError as e:
        return e.code, {}, (time.perf_counter() - t) * 1000
    except Exception:
        return 0, {}, (time.perf_counter() - t) * 1000


def _scenario(base: str, msgs_per_session: int) -> list[tuple[str, int, float]]:
    """1세션: create → chat × N → end. 단계별 (이름, status, latency_ms)."""
    out: list[tuple[str, int, float]] = []
    status, resp, lat = _post(f"{base}/api/conversations",
                              {"customer_name": f"부하{random.randint(1, 99999)}"})
    out.append(("create", status, lat))
    if status != 200 or "id" not in resp:
        return out
    cid = resp["id"]
    for _ in range(msgs_per_session):
        status, _, lat = _post(f"{base}/api/conversations/{cid}/chat",
                                {"message": random.choice(MESSAGES)})
        out.append(("chat", status, lat))
    status, _, lat = _post(f"{base}/api/conversations/{cid}/end",
                            {"rating": random.randint(3, 5)})
    out.append(("end", status, lat))
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
    return s[k]


def _print_summary(rows: list[tuple[str, int, float]], elapsed: float):
    by_op: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    for op, status, lat in rows:
        by_op.setdefault(op, []).append(lat)
        if status >= 400 or status == 0:
            errors[op] = errors.get(op, 0) + 1
    total = sum(len(v) for v in by_op.values())
    print(f"\n총 {total}건 / {elapsed:.1f}초 → {total/elapsed:.1f} req/s")
    print(f"\n{'op':10s}{'n':>6s}{'avg':>8s}{'p50':>8s}{'p95':>8s}{'p99':>8s}{'errs':>6s}")
    for op, lats in by_op.items():
        avg = statistics.mean(lats)
        p50 = _percentile(lats, 50)
        p95 = _percentile(lats, 95)
        p99 = _percentile(lats, 99)
        err = errors.get(op, 0)
        print(f"{op:10s}{len(lats):>6d}{avg:>7.0f}ms{p50:>7.0f}ms{p95:>7.0f}ms{p99:>7.0f}ms{err:>6d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="대상 서버 베이스 URL")
    ap.add_argument("--workers", type=int, default=10, help="동시 워커 수")
    ap.add_argument("--sessions", type=int, default=50, help="총 채팅 세션 수")
    ap.add_argument("--msgs-per-session", type=int, default=3, help="세션당 메시지 수")
    args = ap.parse_args()

    print(f"부하 테스트 시작: {args.url}")
    print(f"  세션 {args.sessions} × 메시지 {args.msgs_per_session}, 동시 워커 {args.workers}")

    all_rows: list[tuple[str, int, float]] = []
    lock = threading.Lock()

    def worker():
        rows = _scenario(args.url, args.msgs_per_session)
        with lock:
            all_rows.extend(rows)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker) for _ in range(args.sessions)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.perf_counter() - started

    _print_summary(all_rows, elapsed)


if __name__ == "__main__":
    main()
