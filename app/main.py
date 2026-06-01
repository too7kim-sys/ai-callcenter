"""AI 콜센터 — FastAPI 진입점."""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import accounts, ai, auth, config, faq
from .database import Base, engine
from .routers import agent, chat, password
from .routers import audit as audit_router
from .routers import auth as auth_router
from .routers import dashboard as dashboard_router
from .routers import roles as roles_router
from .routers import users as users_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_callcenter.main")


def _ensure_columns():
    """기존 DB(이전 버전)에 새로 추가된 컬럼이 없을 때만 ALTER TABLE 로 보강.

    SQLAlchemy `create_all` 은 누락된 테이블만 생성하고 기존 테이블에 컬럼은
    추가하지 않으므로, 모델에 신규 컬럼이 추가되면 기존 DB는 호환되지 않는다.
    여기서는 SQLite 기준으로 가벼운 마이그레이션을 수행한다.
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
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

app = FastAPI(title="AI 콜센터", version="1.0.0")
app.include_router(chat.router)
app.include_router(agent.router)
app.include_router(password.router)
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(roles_router.router)
app.include_router(dashboard_router.router)
app.include_router(audit_router.router)

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
