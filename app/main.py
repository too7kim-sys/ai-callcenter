"""AI 콜센터 — FastAPI 진입점."""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import accounts, ai, config
from .database import Base, engine
from .routers import agent, chat, password

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)
accounts.seed_accounts()

app = FastAPI(title="AI 콜센터", version="1.0.0")
app.include_router(chat.router)
app.include_router(agent.router)
app.include_router(password.router)

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
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
