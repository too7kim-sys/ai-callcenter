"""상담원 콘솔 API: 상담 목록, AI 요약·분류, 답변 추천, 답변 전송, 상태 변경."""
import json

import os
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session, selectinload

from .. import ai, anomaly, audit, auth, cache, faq, ip_allowlist, knowledge, masking, metrics, notifier, realtime, voice
from ..database import get_db
from ..models import AgentUser, Conversation, ConversationNote, KnowledgeItem, Message, ReplyTemplate
from ..permissions import P
from ..schemas import (
    AssignRequest,
    FaqCreate,
    FaqUpdate,
    KnowledgeItemUpdate,
    NoteCreate,
    ReplyRequest,
    StatusRequest,
    TemplateCreate,
    TemplateUpdate,
)
from ..service import (
    build_history,
    finalize_conversation,
    get_conversation_or_404,
    load_agent_map,
    now,
    serialize_conversation,
)

router = APIRouter(prefix="/api", tags=["agent"])

_STATUSES = {"open", "escalated", "closed"}


@router.get("/conversations")
def list_conversations(
    mine: bool = False,
    unassigned: bool = False,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """상담 목록.

    쿼리 파라미터:
      mine=true        — 내가 담당으로 배정된 것만
      unassigned=true  — 미배정만 (긴급 처리 큐)
    """
    q = (
        db.query(Conversation)
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.updated_at.desc())
    )
    if mine:
        q = q.filter(Conversation.assigned_agent_id == user.id)
    if unassigned:
        q = q.filter(Conversation.assigned_agent_id.is_(None))
    convs = q.all()
    agent_map = load_agent_map(db, convs)
    return [serialize_conversation(c, agent_map=agent_map) for c in convs]


@router.post("/conversations/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    payload: AssignRequest,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    """상담을 특정 상담원에게 배정한다. user_id=null 이면 배정 해제."""
    conv = get_conversation_or_404(db, conversation_id)
    if payload.user_id is not None:
        target = db.get(AgentUser, payload.user_id)
        if target is None or not target.active:
            raise HTTPException(status_code=404, detail="대상 상담원을 찾을 수 없습니다.")
        conv.assigned_agent_id = target.id
    else:
        conv.assigned_agent_id = None
    conv.updated_at = now()
    db.commit()
    audit.log(db, user, "conversation.assign",
              target_type="conversation", target_id=conv.id,
              details={"assigned_to": conv.assigned_agent_id})
    realtime.conversation_updated(conv.id, reason="assigned", agent_id=conv.assigned_agent_id)
    return serialize_conversation(conv, include_messages=True, db=db)


@router.post("/conversations/{conversation_id}/analyze")
def analyze_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_ANALYZE)),
):
    """상담 요약·분류: 전체 대화를 요약하고 카테고리/태그를 부여한다."""
    conv = get_conversation_or_404(db, conversation_id)
    result = ai.summarize(build_history(conv))

    conv.summary = result["summary"]
    conv.category = result["category"]
    conv.tags = json.dumps(result["tags"], ensure_ascii=False)
    conv.key_points = json.dumps(result["key_points"], ensure_ascii=False)
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)

    return {
        "conversation": serialize_conversation(conv, include_messages=True),
        "source": result["source"],
    }


@router.post("/conversations/{conversation_id}/recommend")
def recommend_answers(
    conversation_id: int,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_RECOMMEND)),
):
    """상담원 답변 추천: FAQ + 학습된 과거 상담 사례 기반 추천 답변 목록."""
    conv = get_conversation_or_404(db, conversation_id)
    history = build_history(conv)
    last_customer = next(
        (m["content"] for m in reversed(history) if m["role"] == "customer"), ""
    )
    past_cases = knowledge.retrieve(db, last_customer, limit=3)
    result = ai.recommend(history, past_cases=past_cases)
    return {
        "recommendations": result["recommendations"],
        "source": result["source"],
        "past_cases": past_cases,
    }


@router.post("/conversations/{conversation_id}/reply")
def agent_reply(
    conversation_id: int,
    payload: ReplyRequest,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_REPLY)),
):
    """상담원이 직접 답변을 전송한다."""
    conv = get_conversation_or_404(db, conversation_id)
    message = Message(conversation_id=conv.id, role="agent", content=payload.message.strip())
    db.add(message)
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)
    realtime.message_created(conv.id, "agent")
    return serialize_conversation(conv, include_messages=True)


@router.post("/conversations/{conversation_id}/status")
def update_status(
    conversation_id: int,
    payload: StatusRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_CLOSE)),
):
    """상담 상태를 변경한다 (open / escalated / closed).

    closed 로 전환되면 백그라운드에서 자동으로:
      1) AI 요약·카테고리 분류 (이미 분석된 상담은 skip)
      2) 상담원 답변을 RAG 지식 베이스에 학습
    """
    if payload.status not in _STATUSES:
        raise HTTPException(status_code=400, detail="유효하지 않은 상태값입니다.")
    conv = get_conversation_or_404(db, conversation_id)
    conv.status = payload.status
    conv.updated_at = now()
    db.commit()
    db.refresh(conv)
    realtime.conversation_updated(conv.id, status=conv.status, reason="status_change")
    if payload.status == "closed":
        background.add_task(finalize_conversation, conv.id)
    return serialize_conversation(conv, include_messages=True)


@router.get("/knowledge")
def knowledge_stats(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.KNOWLEDGE_VIEW)),
):
    """학습된 상담 지식 통계: 항목 수와 검색 방식(임베딩 가능 여부)."""
    return {"count": knowledge.count(db), "embeddings": ai.embeddings_available()}


@router.get("/knowledge/items")
def list_knowledge_items(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.KNOWLEDGE_VIEW)),
):
    """학습된 지식 전체 목록 (관리·검수용).

    FAQ에 노출되지 않는 짧은 답변 항목도 모두 포함하며, 출처 상담의
    고객명·상태와 임베딩 보유 여부, FAQ 노출 여부를 함께 반환한다.
    """
    items = db.query(KnowledgeItem).order_by(KnowledgeItem.id.desc()).all()
    result = []
    for row in items:
        conv = db.get(Conversation, row.conversation_id)
        answer = row.answer or ""
        result.append({
            "id": row.id,
            "conversation_id": row.conversation_id,
            "customer_name": conv.customer_name if conv else None,
            "conversation_status": conv.status if conv else None,
            "question": row.question,
            "answer": answer,
            "answer_length": len(answer.strip()),
            "faq_visible": len(answer.strip()) >= _MIN_LEARNED_ANSWER_LEN,
            "has_embedding": bool(row.embedding),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return result


@router.patch("/knowledge/items/{item_id}")
def update_knowledge_item(
    item_id: int,
    payload: KnowledgeItemUpdate,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.KNOWLEDGE_DELETE)),
):
    """잘못 학습된 항목의 질문·답변을 수정한다 (검수용).

    질문이 바뀌면 임베딩을 자동으로 재계산해 의미 검색 결과가 갱신되도록 한다.
    """
    import json as _json

    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="학습 항목을 찾을 수 없습니다.")

    question_changed = False
    if payload.question is not None:
        new_q = payload.question.strip()
        if not new_q:
            raise HTTPException(status_code=400, detail="질문을 비울 수 없습니다.")
        if new_q != item.question:
            item.question = new_q
            question_changed = True
    if payload.answer is not None:
        new_a = payload.answer.strip()
        if not new_a:
            raise HTTPException(status_code=400, detail="답변을 비울 수 없습니다.")
        item.answer = new_a

    if question_changed:
        new_emb = ai.embed_text(item.question)
        item.embedding = _json.dumps(new_emb) if new_emb else None

    db.commit()
    return {"ok": True, "id": item.id}


@router.delete("/knowledge/items/{item_id}")
def delete_knowledge_item(
    item_id: int,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.KNOWLEDGE_DELETE)),
):
    """잘못 학습된 항목을 삭제한다 (검수용)."""
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="학습 항목을 찾을 수 없습니다.")
    db.delete(item)
    db.commit()
    return {"ok": True, "deleted_id": item_id}


_MIN_LEARNED_ANSWER_LEN = 20  # 너무 짧은 상담원 답변은 FAQ로 노출하지 않음


@router.post("/faq")
def create_faq(
    payload: FaqCreate,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.FAQ_MANAGE)),
):
    """FAQ 항목 추가 (관리자)."""
    item = faq.create_entry(
        db,
        category=payload.category,
        question=payload.question,
        answer=payload.answer,
        keywords=payload.keywords,
    )
    audit.log(db, user, "faq.create", target_type="faq", target_id=item["id"],
              details={"category": item["category"], "question": item["question"]})
    return {**item, "source": "curated"}


@router.patch("/faq/{entry_id}")
def update_faq(
    entry_id: int,
    payload: FaqUpdate,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.FAQ_MANAGE)),
):
    """FAQ 항목 수정 (관리자)."""
    item = faq.update_entry(
        db,
        entry_id,
        category=payload.category,
        question=payload.question,
        answer=payload.answer,
        keywords=payload.keywords,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="FAQ 항목을 찾을 수 없습니다.")
    audit.log(db, user, "faq.update", target_type="faq", target_id=entry_id)
    return {**item, "source": "curated"}


_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB
_ALLOWED_AUDIO = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".flac", ".webm"}


@router.get("/alerts/status")
def alerts_status(_user=Depends(auth.require_permission(P.AUDIT_VIEW))):
    """외부 알림(Slack/Teams/Discord Webhook) 설정 상태."""
    return {"configured": notifier.is_configured()}


@router.post("/alerts/test")
def alerts_test(
    user=Depends(auth.require_permission(P.AUDIT_VIEW)),
    db: Session = Depends(get_db),
):
    """관리자가 웹훅 URL 설정을 검증하기 위한 동기 테스트 발송."""
    result = notifier.send_test()
    audit.log(db, user, "alerts.test", details={"ok": result.get("ok")})
    return result


@router.get("/admin/metrics")
def admin_metrics(_user=Depends(auth.require_permission(P.AUDIT_VIEW))):
    """엔드포인트별 응답 시간 (p50/p95/p99) + 에러율.

    SSE 스트림은 제외. 메모리 상 최근 1000건 표본 슬라이딩 윈도우.
    """
    return {"summary": metrics.summary(), "endpoints": metrics.stats()}


@router.get("/admin/cache/stats")
def cache_stats(_user=Depends(auth.require_permission(P.AUDIT_VIEW))):
    """인메모리 캐시 통계 (대시보드 캐싱)."""
    return cache.stats()


@router.post("/admin/cache/clear")
def cache_clear(
    prefix: str | None = None,
    user=Depends(auth.require_admin),
    db: Session = Depends(get_db),
):
    """캐시 무효화. prefix 지정 시 일부만, 미지정 시 전체."""
    if prefix:
        removed = cache.invalidate_prefix(prefix)
    else:
        removed = cache.clear_all()
    audit.log(db, user, "cache.clear", details={"prefix": prefix or "*", "removed": removed})
    return {"ok": True, "removed": removed}


@router.get("/security/ip_allowlist")
def ip_allowlist_status(_user=Depends(auth.require_permission(P.AUDIT_VIEW))):
    """IP 화이트리스트 설정 상태 (ALLOWED_IPS).

    enabled=false 면 모든 IP 허용 (운영 시 enabled=true 권장).
    설정 변경은 .env 의 ALLOWED_IPS 수정 + systemctl restart.
    """
    return ip_allowlist.status()


@router.get("/admin/conversations/pending-summary")
def pending_summary_conversations(
    days: int = 7,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.AUDIT_VIEW)),
):
    """종료됐지만 자동 요약 실패/미완료인 상담 목록 — finalize 재시도 대상.

    closed 상태 + summary IS NULL 인 상담을 최근 N일 안에서 수집.
    """
    from datetime import datetime, timedelta, timezone
    days = max(1, min(90, int(days)))
    limit = max(1, min(500, int(limit)))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(Conversation)
          .filter(
              Conversation.status == "closed",
              Conversation.summary.is_(None),
              Conversation.created_at >= since,
          )
          .order_by(Conversation.id.desc())
          .limit(limit)
          .all()
    )
    return {
        "window_days": days,
        "count": len(rows),
        "conversations": [
            {
                "id": c.id,
                "customer_name": c.customer_name,
                "category": c.category,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "message_count": len(c.messages),
            }
            for c in rows
        ],
    }


@router.post("/admin/conversations/finalize-pending")
def retry_finalize_pending(
    background: BackgroundTasks,
    days: int = 7,
    limit: int = 20,
    user=Depends(auth.require_permission(P.CONV_ANALYZE)),
    db: Session = Depends(get_db),
):
    """미완료 finalize 재시도 — 백그라운드로 일괄 처리.

    상담 종료 시점에 AI 호출 실패로 요약/학습이 안 된 상담을 재처리.
    한 번에 너무 많이 돌리면 AI 호출 폭주하므로 limit (기본 20) 제한.
    """
    from datetime import datetime, timedelta, timezone
    days = max(1, min(90, int(days)))
    limit = max(1, min(100, int(limit)))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pending_ids = [
        c.id for c in
        db.query(Conversation.id)
          .filter(
              Conversation.status == "closed",
              Conversation.summary.is_(None),
              Conversation.created_at >= since,
          )
          .order_by(Conversation.id.desc())
          .limit(limit)
          .all()
    ]
    for cid in pending_ids:
        background.add_task(finalize_conversation, cid)
    audit.log(db, user, "conversation.finalize_retry",
              details={"queued": len(pending_ids), "days": days})
    return {
        "queued": len(pending_ids),
        "conversation_ids": pending_ids,
    }


@router.get("/security/anomaly")
def anomaly_status(_user=Depends(auth.require_permission(P.AUDIT_VIEW))):
    """현재 의심 활동 추적 상태 (실패 로그인 / 다중 IP).

    감지 임계값을 초과해 실제 알림으로 이어진 항목은 변경 이력(audit log)
    에 action='anomaly.*' 로 영구 기록된다.
    """
    return anomaly.status()


@router.get("/voice/status")
def voice_status(_user=Depends(auth.require_permission(P.VOICE_UPLOAD))):
    """STT 엔진 가용 여부 및 모델 정보."""
    return {
        "available": voice.is_available(),
        "model": voice.WHISPER_MODEL,
        "language": voice.WHISPER_LANG,
        "error": voice.last_error(),
    }


@router.post("/voice/transcribe")
async def transcribe_voice(
    audio: UploadFile = File(...),
    _user=Depends(auth.require_permission(P.VOICE_UPLOAD)),
):
    """업로드된 음성 파일을 STT 로 변환하고 FAQ 후보를 추출한다.

    응답: {transcript, suggestion: {category, question, answer, keywords, source}}
    """
    if not voice.is_available():
        raise HTTPException(
            status_code=503,
            detail=voice.last_error()
            or "음성 인식 엔진을 사용할 수 없습니다. 서버에 `faster-whisper` 설치가 필요합니다.",
        )
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    if suffix and suffix not in _ALLOWED_AUDIO:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 형식입니다 ({suffix}). " + ", ".join(sorted(_ALLOWED_AUDIO)) + " 만 허용됩니다.",
        )
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다 (25MB 이하).")

    tmp = tempfile.NamedTemporaryFile(suffix=suffix or ".wav", delete=False)
    try:
        tmp.write(content)
        tmp.close()
        try:
            transcript = voice.transcribe(tmp.name)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"음성 변환 중 오류: {exc}")
        if not transcript:
            raise HTTPException(status_code=400, detail="음성에서 텍스트를 추출하지 못했습니다.")
        suggestion = ai.extract_faq_from_transcript(transcript)
        return {
            "transcript": transcript,
            "suggestion": suggestion,
            "filename": audio.filename,
        }
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@router.delete("/faq/{entry_id}")
def delete_faq(
    entry_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.FAQ_MANAGE)),
):
    """FAQ 항목 삭제 (관리자)."""
    if not faq.delete_entry(db, entry_id):
        raise HTTPException(status_code=404, detail="FAQ 항목을 찾을 수 없습니다.")
    audit.log(db, user, "faq.delete", target_type="faq", target_id=entry_id)
    return {"ok": True}


# ====================================================================
# 내부 메모 (상담원 전용)
# ====================================================================

@router.get("/conversations/{conversation_id}/notes")
def list_notes(
    conversation_id: int,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    get_conversation_or_404(db, conversation_id)
    rows = db.query(ConversationNote).filter(
        ConversationNote.conversation_id == conversation_id
    ).order_by(ConversationNote.id.desc()).all()
    result = []
    for n in rows:
        author = db.get(AgentUser, n.author_id) if n.author_id else None
        result.append({
            "id": n.id,
            "content": n.content,
            "author_id": n.author_id,
            "author_name": (author.name or author.username) if author else "(삭제됨)",
            "created_at": n.created_at.isoformat() if n.created_at else None,
        })
    return result


@router.post("/conversations/{conversation_id}/notes")
def create_note(
    conversation_id: int,
    payload: NoteCreate,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    get_conversation_or_404(db, conversation_id)
    n = ConversationNote(
        conversation_id=conversation_id,
        author_id=user.id,
        content=payload.content.strip(),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return {
        "id": n.id,
        "content": n.content,
        "author_id": n.author_id,
        "author_name": user.name or user.username,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.delete("/notes/{note_id}")
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.CONV_VIEW)),
):
    n = db.get(ConversationNote, note_id)
    if n is None:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")
    # 작성자 또는 관리자만 삭제 가능
    if n.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="본인이 작성한 메모만 삭제할 수 있습니다.")
    db.delete(n)
    db.commit()
    return {"ok": True}


# ====================================================================
# 답변 템플릿
# ====================================================================

@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.CONV_REPLY)),
):
    rows = db.query(ReplyTemplate).order_by(ReplyTemplate.id).all()
    return [
        {"id": r.id, "title": r.title, "content": r.content, "category": r.category or ""}
        for r in rows
    ]


@router.post("/templates")
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.TEMPLATE_MANAGE)),
):
    row = ReplyTemplate(
        title=payload.title.strip(),
        content=payload.content.strip(),
        category=(payload.category or "").strip() or None,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.log(db, user, "template.create", target_type="template", target_id=row.id,
              details={"title": row.title})
    return {"id": row.id, "title": row.title, "content": row.content, "category": row.category or ""}


@router.patch("/templates/{template_id}")
def update_template(
    template_id: int,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.TEMPLATE_MANAGE)),
):
    row = db.get(ReplyTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다.")
    if payload.title is not None: row.title = payload.title.strip()
    if payload.content is not None: row.content = payload.content.strip()
    if payload.category is not None: row.category = payload.category.strip() or None
    db.commit()
    audit.log(db, user, "template.update", target_type="template", target_id=row.id)
    return {"id": row.id, "title": row.title, "content": row.content, "category": row.category or ""}


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    user=Depends(auth.require_permission(P.TEMPLATE_MANAGE)),
):
    row = db.get(ReplyTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    audit.log(db, user, "template.delete", target_type="template", target_id=template_id)
    return {"ok": True}


# ====================================================================
# 데이터 내보내기 (CSV)
# ====================================================================

def _csv_response(rows, fields, filename):
    """리스트와 필드명으로 CSV 문자열 응답을 만든다."""
    import csv
    import io
    from fastapi.responses import Response
    buf = io.StringIO()
    # BOM 으로 엑셀 한글 정상 표시
    buf.write("﻿")
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/faq.csv")
def export_faq(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.DATA_EXPORT)),
):
    rows = [
        {"id": f["id"], "category": f["category"], "question": f["question"],
         "answer": f["answer"], "keywords": ", ".join(f.get("keywords", []))}
        for f in faq.FAQS
    ]
    return _csv_response(rows, ["id", "category", "question", "answer", "keywords"], "faq.csv")


@router.get("/export/users.csv")
def export_users(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.DATA_EXPORT)),
):
    rows = []
    for u in db.query(AgentUser).order_by(AgentUser.id).all():
        rows.append({
            "id": u.id, "username": u.username, "name": u.name,
            "email": u.email or "", "role": u.role,
            "active": "Y" if u.active else "N",
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else "",
            "created_at": u.created_at.isoformat() if u.created_at else "",
        })
    return _csv_response(rows, ["id", "username", "name", "email", "role", "active",
                                 "last_login_at", "created_at"], "users.csv")


@router.get("/export/knowledge.csv")
def export_knowledge(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.DATA_EXPORT)),
):
    """학습 데이터 CSV 내보내기. 대량 유출 방어를 위해 PII 마스킹 적용."""
    rows = []
    for it in db.query(KnowledgeItem).order_by(KnowledgeItem.id).all():
        rows.append({
            "id": f"L{it.id}", "conversation_id": it.conversation_id,
            "question": masking.mask_text(it.question),
            "answer": masking.mask_text(it.answer),
            "has_embedding": "Y" if it.embedding else "N",
            "created_at": it.created_at.isoformat() if it.created_at else "",
        })
    return _csv_response(rows, ["id", "conversation_id", "question", "answer",
                                 "has_embedding", "created_at"], "knowledge.csv")


@router.get("/export/conversations.csv")
def export_conversations(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.DATA_EXPORT)),
):
    """상담 메타데이터 CSV 내보내기. customer_feedback 은 PII 마스킹 적용.

    customer_name 은 운영상 식별 용도로 그대로 노출 (이미 가명 가능).
    """
    rows = []
    for c in db.query(Conversation).order_by(Conversation.id).all():
        agent_name = ""
        if c.assigned_agent_id:
            a = db.get(AgentUser, c.assigned_agent_id)
            if a: agent_name = a.name or a.username
        rows.append({
            "id": c.id, "customer_name": c.customer_name, "status": c.status,
            "category": c.category or "", "sentiment": c.sentiment or "",
            "risk_level": c.risk_level or "", "message_count": len(c.messages),
            "assigned_agent": agent_name,
            "customer_rating": c.customer_rating or "",
            "customer_feedback": masking.mask_text(c.customer_feedback) or "",
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "updated_at": c.updated_at.isoformat() if c.updated_at else "",
        })
    return _csv_response(rows, ["id", "customer_name", "status", "category", "sentiment",
                                 "risk_level", "message_count", "assigned_agent",
                                 "customer_rating", "customer_feedback",
                                 "created_at", "updated_at"], "conversations.csv")


@router.get("/faq")
def get_faq(
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.FAQ_VIEW)),
):
    """FAQ 지식베이스 조회 — 정적 FAQ + 종료된 상담에서 학습된 항목을 함께 반환한다.

    각 항목에 `source`("curated"|"learned") 가 포함되며, 학습 항목은
    출처 상담 ID(`conversation_id`)와 학습 시각(`learned_at`) 을 함께 노출한다.
    """
    items = [{**entry, "source": "curated"} for entry in faq.FAQS]

    learned_rows = db.query(KnowledgeItem).order_by(KnowledgeItem.id).all()
    for row in learned_rows:
        if not row.answer or len(row.answer.strip()) < _MIN_LEARNED_ANSWER_LEN:
            continue  # 너무 짧은 답변은 FAQ 노출 제외
        conv = db.get(Conversation, row.conversation_id)
        category = (conv.category if conv and conv.category
                    else ai.classify_category(row.question))
        items.append({
            "id": f"L{row.id}",
            "category": category,
            "question": row.question,
            "answer": row.answer,
            "keywords": [],
            "source": "learned",
            "conversation_id": row.conversation_id,
            "learned_at": row.created_at.isoformat() if row.created_at else None,
        })
    return items
