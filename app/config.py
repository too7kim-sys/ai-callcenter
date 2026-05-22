"""환경 설정. ANTHROPIC_API_KEY 유무로 AI 동작 모드를 결정한다."""
import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7").strip() or "claude-opus-4-7"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./callcenter.db").strip()

# 키가 있으면 실제 Claude API, 없으면 모의 응답
AI_MODE = "live" if ANTHROPIC_API_KEY else "mock"
