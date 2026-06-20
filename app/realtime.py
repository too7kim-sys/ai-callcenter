"""실시간 이벤트 브로커 (Server-Sent Events 용).

설계:
  • 인메모리 pub/sub. 각 SSE 연결마다 asyncio.Queue 한 개를 보유.
  • publish() 는 sync/async 어디서든 호출 가능 — 각 구독자의 이벤트 루프로
    call_soon_threadsafe 로 안전하게 위임한다.
  • subscriber 가 느려 큐가 가득 차면 새 이벤트는 drop (서비스 안정성 우선).

이벤트 형식 (dict):
  {
    "type": "conversation_created" | "conversation_updated" | "message_created",
    "conversation_id": <int>,
    "role": "customer" | "ai" | "agent",   (message 만)
    "ts": <ISO8601>,
  }
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger("ai_callcenter.realtime")

_QUEUE_MAX = 100

_lock = threading.Lock()
# (queue, loop, filter_fn or None)
_subscribers: list[tuple[asyncio.Queue, asyncio.AbstractEventLoop, Callable | None]] = []

# 재연결 시 중복 차단을 위한 최근 이벤트 링버퍼.
# 각 이벤트에 단조증가 id 부여 → SSE 의 'id:' 라인으로 전송.
# 클라이언트가 끊겼다 재연결하면 Last-Event-ID 헤더로 마지막 id 를 보내고,
# 서버는 그 이후 이벤트만 replay.
_HISTORY_MAX = 500
_history: deque[tuple[int, dict]] = deque(maxlen=_HISTORY_MAX)
_history_lock = threading.Lock()
_next_event_id = 0


def publish(event: dict) -> None:
    """이벤트 발행 (sync / async 모두에서 호출 가능)."""
    global _next_event_id
    with _history_lock:
        _next_event_id += 1
        event_id = _next_event_id
        event = {**event,
                 "id": event_id,
                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _history.append((event_id, event))

    with _lock:
        subs = list(_subscribers)
    for queue, loop, filt in subs:
        try:
            if filt is not None and not filt(event):
                continue
            loop.call_soon_threadsafe(_safe_put, queue, event)
        except RuntimeError:
            pass


def replay_since(last_id: int, filter_fn: Callable | None = None) -> tuple[list[dict], bool]:
    """Last-Event-ID 이후의 이벤트를 링버퍼에서 가져온다.

    반환: (events, gap)
      events — replay 할 이벤트 목록
      gap    — 링버퍼 한계로 일부 이벤트가 사라진 경우 True.
               클라이언트는 gap=True 시 풀 리프레시 (목록 재조회) 권장.
    """
    with _history_lock:
        snapshot = list(_history)
    if not snapshot:
        return [], False
    oldest_id = snapshot[0][0]
    # 클라이언트가 요청한 last_id 가 가장 오래된 버퍼 ID 보다 작으면
    # (last_id < oldest_id - 1) → 그 사이 이벤트가 링에서 빠져나갔다 = gap.
    # last_id == oldest_id - 1 은 정상 (다음 이벤트가 oldest_id).
    gap = last_id > 0 and last_id < oldest_id - 1
    out: list[dict] = []
    for eid, event in snapshot:
        if eid <= last_id:
            continue
        if filter_fn is not None and not filter_fn(event):
            continue
        out.append(event)
    return out, gap


def _safe_put(queue: asyncio.Queue, event: dict):
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.debug("subscriber 큐 가득 — 이벤트 drop type=%s", event.get("type"))


async def subscribe(filter_fn: Callable | None = None):
    """async generator — 이벤트를 yield. 구독 해제는 generator 종료 시 자동."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    loop = asyncio.get_running_loop()
    entry = (queue, loop, filter_fn)
    with _lock:
        _subscribers.append(entry)
    try:
        while True:
            yield await queue.get()
    finally:
        with _lock:
            try:
                _subscribers.remove(entry)
            except ValueError:
                pass


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


# --------------------------------------------------------------------
# 편의 함수 — 라우터에서 한 줄로 부르기 위함
# --------------------------------------------------------------------

def conversation_created(conversation_id: int):
    publish({"type": "conversation_created", "conversation_id": conversation_id})


def conversation_updated(conversation_id: int, **extra):
    publish({"type": "conversation_updated", "conversation_id": conversation_id, **extra})


def message_created(conversation_id: int, role: str):
    publish({"type": "message_created", "conversation_id": conversation_id, "role": role})
