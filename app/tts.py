"""텍스트 → 음성 변환 (TTS) 모듈.

엔진 우선순위 (TTS_ENGINE 환경변수로 강제 가능):
  1. Edge TTS  — Microsoft Edge 의 무료 온라인 TTS.
                 한국어 음성 품질 우수, API 키 불필요. (pip install edge-tts)
  2. espeak-ng — 오프라인 폴백. 기계적이지만 설치만 돼 있으면 항상 동작.

둘 다 사용할 수 없으면 is_available() 이 False 를 반환하고 라우터는 503.

음성 ID (Edge):
  ko-KR-SunHiNeural    (여, 친근)        ← 기본값
  ko-KR-InJoonNeural   (남, 친근)
  ko-KR-HyunsuNeural   (남)
  ko-KR-BongJinNeural  (남)
  ko-KR-GookMinNeural  (남)
  ko-KR-JiMinNeural    (여)
  ko-KR-SeoHyeonNeural (여)
  ko-KR-YuJinNeural    (여)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger("ai_callcenter.tts")

DEFAULT_VOICE = os.getenv("TTS_VOICE", "ko-KR-SunHiNeural").strip() or "ko-KR-SunHiNeural"
DEFAULT_LANG = os.getenv("TTS_LANG", "ko").strip() or "ko"
# auto | edge | espeak
TTS_ENGINE = os.getenv("TTS_ENGINE", "auto").strip().lower() or "auto"

_MAX_TEXT_LEN = 5000

_KO_EDGE_VOICES = [
    ("ko-KR-SunHiNeural",    "선희 (여, 친근)"),
    ("ko-KR-InJoonNeural",   "인준 (남, 친근)"),
    ("ko-KR-HyunsuNeural",   "현수 (남)"),
    ("ko-KR-BongJinNeural",  "봉진 (남)"),
    ("ko-KR-GookMinNeural",  "국민 (남)"),
    ("ko-KR-JiMinNeural",    "지민 (여)"),
    ("ko-KR-SeoHyeonNeural", "서현 (여)"),
    ("ko-KR-YuJinNeural",    "유진 (여)"),
]


# --------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------

def is_available() -> bool:
    return _resolve_engine() is not None


def engine_info() -> dict:
    engine = _resolve_engine()
    return {
        "available": engine is not None,
        "engine": engine,
        "default_voice": DEFAULT_VOICE,
        "edge_tts_installed": _has_edge_tts(),
        "espeak_installed": bool(shutil.which("espeak-ng")),
    }


def list_voices() -> list[dict]:
    """UI 의 보이스 셀렉트박스용 — 사용 가능 엔진 기준 필터링."""
    out = []
    if _has_edge_tts():
        for vid, name in _KO_EDGE_VOICES:
            out.append({"id": vid, "name": name, "engine": "edge"})
    if shutil.which("espeak-ng"):
        out.append({"id": "espeak", "name": "espeak (오프라인 폴백)", "engine": "espeak"})
    return out


async def synthesize(text: str, voice: str | None = None) -> tuple[bytes, str]:
    """텍스트 → (audio_bytes, mime_type) 반환.

    voice 가 'espeak' 거나 Edge 로 실패하면 자동으로 espeak-ng 로 폴백.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("빈 텍스트는 합성할 수 없습니다.")
    if len(text) > _MAX_TEXT_LEN:
        raise ValueError(f"텍스트가 너무 깁니다 ({_MAX_TEXT_LEN}자 이하).")

    engine = _resolve_engine()
    if engine is None:
        raise RuntimeError(
            "TTS 엔진을 사용할 수 없습니다. "
            "`pip install edge-tts` 또는 `apt-get install espeak-ng` 후 재시도하세요."
        )

    voice = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    force_espeak = (voice.lower() == "espeak")

    if engine == "edge" and not force_espeak:
        try:
            data = await _synthesize_edge(text, voice)
            return data, "audio/mpeg"
        except Exception as exc:
            if not shutil.which("espeak-ng"):
                raise
            logger.warning("Edge TTS 실패 → espeak-ng 로 폴백: %s", exc)
    return _synthesize_espeak(text), "audio/wav"


# --------------------------------------------------------------------
# 내부
# --------------------------------------------------------------------

def _resolve_engine() -> str | None:
    if TTS_ENGINE in ("edge", "auto") and _has_edge_tts():
        return "edge"
    if TTS_ENGINE in ("espeak", "auto") and shutil.which("espeak-ng"):
        return "espeak"
    return None


def _has_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


async def _synthesize_edge(text: str, voice: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        raise RuntimeError("Edge TTS 가 오디오 데이터를 반환하지 않았습니다.")
    return b"".join(chunks)


def _synthesize_espeak(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = tmp.name
    try:
        subprocess.run(
            ["espeak-ng", "-v", "ko", "-s", "150", text, "-w", out_path],
            check=True,
            capture_output=True,
            timeout=30,
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
