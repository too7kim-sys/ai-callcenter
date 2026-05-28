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

AI_PROVIDER = (os.getenv("AI_PROVIDER", "ollama").strip().lower() or "ollama")
if AI_PROVIDER not in {"auto", "claude", "ollama", "mock"}:
    AI_PROVIDER = "ollama"

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7").strip() or "claude-opus-4-7"

# --- Ollama (로컬/원격 LLM) ---
# 기본값은 사내 Ollama 서버를 가리킨다. 다른 환경에서는 .env 의
# OLLAMA_BASE_URL 로 override.
OLLAMA_BASE_URL = (
    os.getenv("OLLAMA_BASE_URL", "http://192.168.45.214:11434").strip()
    or "http://192.168.45.214:11434"
)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1").strip() or "llama3.1"
# 상담 학습(RAG) 시 의미 검색에 사용할 임베딩 모델 (Ollama)
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text").strip() or "nomic-embed-text"

# --- 이메일 (비밀번호 재설정 링크 발송). SMTP_HOST 미설정 시 서버 로그로 폴백. ---
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
except ValueError:
    SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@ai-callcenter.local").strip() or "no-reply@ai-callcenter.local"

# 비밀번호 재설정 링크의 기본 URL. 미설정 시 요청 URL을 사용한다.
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
