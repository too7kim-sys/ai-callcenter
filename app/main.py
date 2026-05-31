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

Base.metadata.create_all(bind=engine)
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
