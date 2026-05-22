"""환경 설정.

AI_PROVIDER 로 AI 제공자를 선택한다:
  auto   - Claude 키가 있으면 Claude, 없으면 Ollama, 둘 다 없으면 mock (기본값)
  claude - 실제 Claude API
  ollama - 로컬 Ollama 서버
  mock   - 모의 응답
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- 공통 ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./callcenter.db").strip()

AI_PROVIDER = (os.getenv("AI_PROVIDER", "auto").strip().lower() or "auto")
if AI_PROVIDER not in {"auto", "claude", "ollama", "mock"}:
    AI_PROVIDER = "auto"

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7").strip() or "claude-opus-4-7"

# --- Ollama (로컬 LLM) ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip() or "http://localhost:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1").strip() or "llama3.1"
# 상담 학습(RAG) 시 의미 검색에 사용할 임베딩 모델 (Ollama)
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text").strip() or "nomic-embed-text"
