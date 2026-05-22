"""AI 콜센터 — FastAPI 진입점."""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import config
from .database import Base, engine
from .routers import agent, chat

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI 콜센터", version="1.0.0")
app.include_router(chat.router)
app.include_router(agent.router)

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)


@app.get("/api/config")
def api_config():
    """프런트엔드용 설정: AI 동작 모드(live/mock)와 모델명."""
    return {"ai_mode": config.AI_MODE, "model": config.CLAUDE_MODEL}


@app.get("/")
def customer_page():
    """고객 채팅 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "customer.html"))


@app.get("/agent")
def agent_page():
    """상담원 콘솔 화면."""
    return FileResponse(os.path.join(STATIC_DIR, "agent.html"))
