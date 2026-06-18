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
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger("ai_callcenter.realtime")

_QUEUE_MAX = 100

_lock = threading.Lock()
# (queue, loop, filter_fn or None)
_subscribers: list[tuple[asyncio.Queue, asyncio.AbstractEventLoop, Callable | None]] = []


def publish(event: dict) -> None:
    """이벤트 발행 (sync / async 모두에서 호출 가능)."""
    event = {**event, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _lock:
        subs = list(_subscribers)
    for queue, loop, filt in subs:
        try:
            if filt is not None and not filt(event):
                continue
            loop.call_soon_threadsafe(_safe_put, queue, event)
        except RuntimeError:
            # 루프 종료됨 — 다음 정리 사이클에 제거됨
            pass


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
