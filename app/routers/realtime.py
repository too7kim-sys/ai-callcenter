"""Server-Sent Events 엔드포인트 — 상담원/고객 실시간 업데이트.

  GET /api/events/stream                  — 전체 이벤트 (상담원 콘솔용)
  GET /api/events/stream?conversation_id=N — 해당 상담만 (고객 채팅용)

페이로드는 가벼운 메타데이터(conversation_id, type) 만 전달한다.
클라이언트는 이벤트 수신 시 기존 GET API 로 해당 부분만 다시 읽어와
업데이트 — 권한 검사·직렬화 로직을 SSE 측에 중복하지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import realtime
from ..database import get_db
from ..models import Conversation

logger = logging.getLogger("ai_callcenter.realtime.router")

router = APIRouter(prefix="/api", tags=["realtime"])

_KEEPALIVE_INTERVAL = 15  # 초 — 프록시/브라우저 idle timeout 방지


@router.get("/events/stream")
async def stream_events(
    request: Request,
    conversation_id: int | None = Query(None, description="지정 시 해당 상담만 구독"),
    db: Session = Depends(get_db),
):
    filter_fn = None
    if conversation_id is not None:
        if db.get(Conversation, conversation_id) is None:
            raise HTTPException(status_code=404, detail="상담을 찾을 수 없습니다.")
        target = conversation_id  # closure 캡처
        filter_fn = lambda ev: ev.get("conversation_id") == target

    async def event_stream():
        # 즉시 응답 시작 (브라우저 EventSource 의 onopen 트리거)
        yield ":connected\n\n"
        try:
            async for event in _merge_with_keepalive(realtime.subscribe(filter_fn)):
                if await request.is_disconnected():
                    break
                if event is None:
                    # 키프어라이브 — SSE 주석 라인 (브라우저는 무시)
                    yield ":keepalive\n\n"
                    continue
                ev_type = event.get("type", "message")
                yield f"event: {ev_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx 버퍼링 비활성화
        },
    )


@router.get("/events/status")
def events_status():
    """SSE 디버그 — 현재 구독자 수."""
    return {"subscribers": realtime.subscriber_count()}


async def _merge_with_keepalive(stream):
    """이벤트 스트림과 주기적 keepalive(None) 를 합쳐 yield."""
    iterator = stream.__aiter__()
    next_task = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait(
                {next_task}, timeout=_KEEPALIVE_INTERVAL,
            )
            if not done:
                yield None  # keepalive 신호
                continue
            try:
                ev = next_task.result()
            except StopAsyncIteration:
                return
            yield ev
            next_task = asyncio.create_task(iterator.__anext__())
    finally:
        if not next_task.done():
            next_task.cancel()
