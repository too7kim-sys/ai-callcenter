"""AI 콜센터 — FastAPI 진입점."""
import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import time

from . import accounts, ai, auth, backup, config, faq, metrics
from .database import Base, engine
from .security_headers import SecurityHeadersMiddleware
from .routers import agent, callback, chat, password
from .routers import audit as audit_router
from .routers import auth as auth_router
from .routers import dashboard as dashboard_router
from .routers import realtime as realtime_router
from .routers import roles as roles_router
from .routers import users as users_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_callcenter.main")


def _ensure_columns():
    """기존 DB(이전 버전)에 새로 추가된 컬럼·인덱스가 없을 때만 ALTER/CREATE.

    SQLAlchemy `create_all` 은 누락된 테이블만 생성하고 기존 테이블의 컬럼·
    인덱스는 건드리지 않으므로, 모델 변경 시 기존 DB 는 호환되지 않는다.
    여기서는 SQLite 기준으로 가벼운 마이그레이션을 수행한다.
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)

    # 인덱스 — 대시보드/목록의 created_at·status 필터가 핫패스
    indexes = [
        ("conversations", "ix_conversations_status",     "status"),
        ("conversations", "ix_conversations_created_at", "created_at"),
        ("conversations", "ix_conversations_updated_at", "updated_at"),
        ("messages",      "ix_messages_created_at",      "created_at"),
    ]
    for table, idx_name, column in indexes:
        if table not in insp.get_table_names():
            continue
        existing = {i["name"] for i in insp.get_indexes(table)}
        if idx_name in existing:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"))
            log.info("마이그레이션: 인덱스 %s 추가", idx_name)
        except Exception as e:
            log.warning("인덱스 추가 실패 (%s): %s", idx_name, e)
    expected = {
        "conversations": [
            ("assigned_agent_id", "INTEGER"),
            ("agent_requested", "BOOLEAN DEFAULT 0"),
            ("customer_rating", "INTEGER"),
            ("customer_feedback", "TEXT"),
        ],
        "messages": [
            ("feedback", "VARCHAR"),
        ],
        "agent_users": [
            ("totp_secret", "VARCHAR"),
            ("totp_enabled", "BOOLEAN DEFAULT 0"),
            ("totp_recovery", "TEXT"),
        ],
    }
    for table, cols in expected.items():
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name in existing:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                log.info("마이그레이션: %s.%s 컬럼 추가", table, name)
            except Exception as e:  # 다른 DB 엔진 / 이미 추가됨 등
                log.warning("마이그레이션 실패 (%s.%s): %s", table, name, e)


Base.metadata.create_all(bind=engine)
_ensure_columns()
auth.seed_roles()      # 시스템 역할(admin, agent)
accounts.seed_accounts()
auth.seed_admin()
faq.init_db()          # FAQ 시드 + 캐시 로딩
backup.start_scheduler()  # 자동 백업 데몬 (BACKUP_ENABLED=false 면 no-op)

app = FastAPI(title="AI 콜센터", version="1.0.0")
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def _record_metrics(request, call_next):
    """엔드포인트별 응답 시간 측정. SSE 스트림은 제외 (오래 열려있어 왜곡)."""
    if request.url.path.startswith("/api/events/stream"):
        return await call_next(request)
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - start) * 1000
    metrics.record(request.method, request.url.path, response.status_code, latency_ms)
    return response


app.include_router(chat.router)
app.include_router(agent.router)
app.include_router(password.router)
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(roles_router.router)
app.include_router(dashboard_router.router)
app.include_router(audit_router.router)
app.include_router(realtime_router.router)
app.include_router(callback.router)

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)

# 공유 자산(상단 메뉴 등)을 /assets/ 에서 서비스
app.mount(
    "/assets",
    StaticFiles(directory=os.path.join(STATIC_DIR, "assets")),
    name="assets",
)


@app.get("/api/config")
def api_config():
    """프런트엔드용 설정: AI 제공자(claude/ollama/mock)와 모델명."""
    mode = ai.get_ai_mode()
    model = {"claude": config.CLAUDE_MODEL, "ollama": config.OLLAMA_MODEL}.get(mode, "—")
    return {"ai_mode": mode, "model": model}


# ====================================================================
# 헬스체크 (LB / 컨테이너 오케스트레이션 용)
# ====================================================================

@app.get("/healthz")
def healthz():
    """Liveness — 프로세스가 떠 있으면 200. 외부 의존성 검사 X."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness — DB 까지 도달 가능해야 200, 아니면 503."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "error": str(exc)[:200]},
        )
    return {"status": "ready"}


# ====================================================================
# 백업 관리 (admin 전용)
# ====================================================================

@app.get("/api/admin/backup/status")
def backup_status(_user=Depends(auth.require_admin)):
    """현재 백업 설정 + 최근 백업 정보."""
    return backup.status()


@app.get("/api/admin/backup/list")
def backup_list(_user=Depends(auth.require_admin)):
    return backup.list_backups()


@app.post("/api/admin/backup/run")
def backup_run(_user=Depends(auth.require_admin)):
    """수동 백업 트리거 (테스트·일회성 스냅샷용)."""
    try:
        path = backup.backup_now()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"백업 실패: {exc}")
    removed = backup.cleanup_old_backups()
    return {"ok": True, "file": path.name, "cleaned": removed}


@app.get("/")
def customer_page():
    """고객 채팅 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "customer.html"))


@app.get("/agent")
def agent_page():
    """상담원 콘솔 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "agent.html"))


@app.get("/reset")
def reset_page():
    """비밀번호 재설정 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "reset.html"))


@app.get("/faq")
def faq_page():
    """FAQ 지식베이스 뷰어 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "faq.html"))


@app.get("/knowledge")
def knowledge_page():
    """학습 데이터 관리 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "knowledge.html"))


@app.get("/login")
def login_page():
    """콜센터 사용자 로그인 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


@app.get("/users")
def users_page():
    """콜센터 사용자 관리 화면 (관리자 전용)."""
    return FileResponse(os.path.join(STATIC_DIR, "users.html"))


@app.get("/permissions")
def permissions_page():
    """역할·권한 관리 화면 (관리자 전용)."""
    return FileResponse(os.path.join(STATIC_DIR, "permissions.html"))


@app.get("/dashboard")
def dashboard_page():
    """대시보드 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/audit")
def audit_page():
    """변경 이력 화면 (관리자 전용)."""
    return FileResponse(os.path.join(STATIC_DIR, "audit.html"))


@app.get("/templates")
def templates_page():
    """답변 템플릿 관리 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "templates.html"))


@app.get("/callbacks")
def callbacks_page():
    """콜백 큐 관리 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "callbacks.html"))


@app.get("/security")
def security_page():
    """본인 보안 설정 (2FA)."""
    return FileResponse(os.path.join(STATIC_DIR, "security.html"))


@app.get("/metrics")
def metrics_page():
    """관리자 — 응답 시간/캐시 메트릭."""
    return FileResponse(os.path.join(STATIC_DIR, "metrics.html"))


@app.get("/widget")
def widget_page():
    """임베디드 채팅 위젯 (iframe 안에서 동작하는 컴팩트 UI)."""
    return FileResponse(os.path.join(STATIC_DIR, "widget.html"))


@app.get("/widget/loader.js")
def widget_loader():
    """외부 사이트에 한 줄로 삽입하는 로더 스크립트."""
    return FileResponse(
        os.path.join(STATIC_DIR, "assets", "widget-loader.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/widget/demo")
def widget_demo_page():
    """위젯 동작 확인용 외부 사이트 시뮬레이션 페이지."""
    return FileResponse(os.path.join(STATIC_DIR, "widget-demo.html"))


# ====================================================================
# PWA — manifest / service worker / offline 폴백
# ====================================================================

@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def pwa_service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        # SW 는 즉시 갱신이 중요 — 캐시 헤더 최소화
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Service-Worker-Allowed": "/"},
    )


@app.get("/offline.html")
def pwa_offline():
    return FileResponse(os.path.join(STATIC_DIR, "offline.html"))
