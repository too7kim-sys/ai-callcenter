"""고객 채팅 API: 상담 생성, AI 챗봇 응답, 상담 조회."""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from datetime import datetime

from .. import ai, config, knowledge, notifier, ratelimit, tts
from ..database import get_db
from ..models import Conversation, Message
from ..schemas import (
    AgentRequestBody,
    ChatRequest,
    ConversationCreate,
    CustomerEndRequest,
    FeedbackRequest,
    TtsRequest,
)
from ..service import (
    build_history,
    finalize_conversation,
    get_conversation_or_404,
    now,
    serialize_conversation,
)


def _business_hours_active() -> bool:
    """현재 시각이 영업 시간(평일 09~18시) 내인지."""
    nowt = datetime.now()
    if nowt.weekday() >= 5:  # 토(5), 일(6)
        return False
    return config.BUSINESS_START_HOUR <= nowt.hour < config.BUSINESS_END_HOUR

router = APIRouter(prefix="/api", tags=["customer"])


@router.post(
    "/conversations",
    dependencies=[Depends(ratelimit.rate_limit("conv_create", 5, 60))],
)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)):
    """새 상담 시작 (IP 당 5/분)."""
    conv = Conversation(customer_name=(payload.customer_name or "고객").strip() or "고객")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv, include_messages=True)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    """상담 상세 + 메시지 조회 (고객/상담원 공용)."""
    conv = get_conversation_or_404(db, conversation_id)
    return serialize_conversation(conv, include_messages=True, db=db)


@router.get("/voice/tts/status")
def tts_status():
    """TTS 엔진 가용 여부 + 기본 음성."""
    return tts.engine_info()


@router.get("/voice/tts/voices")
def tts_voices():
    """사용 가능한 음성 목록 (UI 셀렉트박스용)."""
    return tts.list_voices()


@router.post(
    "/voice/tts",
    dependencies=[Depends(ratelimit.rate_limit("tts", 20, 60))],
)
async def tts_synthesize(payload: TtsRequest):
    """텍스트를 한국어 음성으로 변환해 오디오 바이트로 반환한다.

    Edge TTS 가 가능하면 자연스러운 한국어, 불가능하면 espeak-ng 폴백.
    응답은 audio/mpeg 또는 audio/wav 로 브라우저 <audio> 태그에서 바로 재생 가능.
    """
    if not tts.is_available():
        raise HTTPException(
            status_code=503,
            detail="TTS 엔진을 사용할 수 없습니다. "
                   "`pip install edge-tts` 또는 `apt-get install espeak-ng` 후 재시도하세요.",
        )
    try:
        data, mime = await tts.synthesize(payload.text, payload.voice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"음성 합성 중 오류: {exc}")
    return Response(content=data, media_type=mime)


@router.get("/business-status")
def business_status():
    """영업 시간 안내 (고객 채팅 진입 시 배너)."""
    active = _business_hours_active()
    return {
        "active": active,
        "start_hour": config.BUSINESS_START_HOUR,
        "end_hour": config.BUSINESS_END_HOUR,
        "message": (
            f"상담원 응대 시간 (평일 {config.BUSINESS_START_HOUR}:00 ~ "
            f"{config.BUSINESS_END_HOUR}:00). 시간 외에는 AI 상담봇이 응대합니다."
        ) if not active else None,
    }


@router.post("/messages/{message_id}/feedback")
def feedback_message(
    message_id: int,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
):
    """고객이 AI/상담원 답변에 👍/👎 피드백 (공개). 부정 피드백 누적 시 자동 에스컬레이션."""
    msg = db.get(Message, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    if msg.role not in ("ai", "agent"):
        raise HTTPException(status_code=400, detail="고객 메시지에는 평가할 수 없습니다.")
    v = (payload.value or "").strip().lower()
    msg.feedback = v if v in ("up", "down") else None
    db.commit()
    # 부정 피드백이면 해당 상담을 에스컬레이션 후보로 표시
    if msg.feedback == "down":
        conv = db.get(Conversation, msg.conversation_id)
        if conv and conv.status == "open":
            conv.status = "escalated"
            conv.updated_at = now()
            db.commit()
            notifier.notify_escalation(
                conversation_id=conv.id,
                customer_name=conv.customer_name,
                reason="고객 부정 피드백(👎)",
                risk_level=conv.risk_level,
                last_message=msg.content,
            )
    return {"ok": True, "feedback": msg.feedback}


@router.post("/conversations/{conversation_id}/agent-request")
def request_agent(
    conversation_id: int,
    payload: AgentRequestBody,
    db: Session = Depends(get_db),
):
    """고객이 명시적으로 '상담원 연결' 요청. 상태를 에스컬레이션으로 전환."""
    conv = get_conversation_or_404(db, conversation_id)
    was_open = conv.status == "open"
    conv.agent_requested = True
    if was_open:
        conv.status = "escalated"
    conv.updated_at = now()
    note = "(고객 상담원 연결 요청)"
    if payload.note.strip():
        note += f" {payload.note.strip()[:300]}"
    sys_msg = Message(conversation_id=conv.id, role="ai", content=note)
    db.add(sys_msg)
    db.commit()
    db.refresh(conv)
    if was_open:
        notifier.notify_escalation(
            conversation_id=conv.id,
            customer_name=conv.customer_name,
            reason="고객이 상담원 연결을 요청",
            risk_level=conv.risk_level,
            last_message=payload.note.strip() or None,
        )
    return serialize_conversation(conv, include_messages=True, db=db)


@router.post("/conversations/{conversation_id}/end")
def end_conversation(
    conversation_id: int,
    payload: CustomerEndRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """고객이 상담 종료 + (선택) 만족도 평가.

    종료 직후 응답을 보낸 뒤, 백그라운드에서 AI 자동 요약·분류와 RAG 학습을
    수행한다 (응답 지연 0). 다음 상담 자동 추천 품질이 시간이 갈수록 향상.
    """
    conv = get_conversation_or_404(db, conversation_id)
    if payload.rating is not None:
        conv.customer_rating = payload.rating
    if payload.feedback:
        conv.customer_feedback = payload.feedback.strip()[:1000]
    conv.status = "closed"
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)
    background.add_task(finalize_conversation, conv.id)
    return serialize_conversation(conv, include_messages=True, db=db)


@router.post(
    "/conversations/{conversation_id}/chat",
    dependencies=[Depends(ratelimit.rate_limit("chat", 30, 60))],
)
async def chat(conversation_id: int, payload: ChatRequest, db: Session = Depends(get_db)):
    """고객 메시지 수신 → (감정 분석 ∥ 학습검색+AI 답변) 병렬 → 저장.

    감정 분석과 답변 생성은 서로 독립이므로 병렬 실행해 체감 지연을
    절반 수준으로 줄인다 (원격 Ollama 기준 ~6초 → ~3초).
    """
    conv = get_conversation_or_404(db, conversation_id)
    message = payload.message.strip()

    # 답변 생성용 history 는 '이번 고객 메시지를 포함'해야 한다.
    history = build_history(conv) + [{"role": "customer", "content": message}]

    def _reply_pipeline():
        past_cases = knowledge.retrieve(db, message, limit=3)
        return ai.generate_reply(history, past_cases=past_cases)

    # 1) 두 AI 호출을 동시에 실행
    sentiment, reply = await asyncio.gather(
        asyncio.to_thread(ai.analyze_sentiment, message),
        asyncio.to_thread(_reply_pipeline),
    )

    # 2) 고객 메시지 + AI 답변 저장 (1회 commit 으로 줄임)
    db.add(Message(
        conversation_id=conv.id,
        role="customer",
        content=message,
        sentiment=sentiment["sentiment"],
        sentiment_score=sentiment["score"],
    ))
    db.add(Message(conversation_id=conv.id, role="ai", content=reply["reply"]))
    prev_status = conv.status
    conv.sentiment = sentiment["sentiment"]
    conv.sentiment_score = sentiment["score"]
    conv.risk_level = sentiment["risk_level"]
    newly_escalated = (
        sentiment["risk_level"] == "high"
        and conv.status != "closed"
        and prev_status != "escalated"
    )
    if sentiment["risk_level"] == "high" and conv.status != "closed":
        conv.status = "escalated"
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)

    # 새로 고위험으로 전환된 경우에만 외부 알림 발송 (중복 방지)
    if newly_escalated:
        notifier.notify_escalation(
            conversation_id=conv.id,
            customer_name=conv.customer_name,
            reason="AI 감정 분석에서 고위험 신호 감지",
            risk_level="high",
            last_message=message,
        )

    return {
        "conversation": serialize_conversation(conv, include_messages=True),
        "sentiment": sentiment,
        "ai_source": reply["source"],
    }
