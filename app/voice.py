"""음성 파일 → 텍스트 변환 (STT) 모듈.

faster-whisper 가 설치되어 있고 모델을 받을 수 있으면 활성화된다.
미설치 환경에서는 is_available() 이 False 를 반환하고 라우터에서
503 으로 안내한다 (배포 환경에 따라 자유롭게 활성화 가능).
"""
import logging
import os
import threading

logger = logging.getLogger("ai_callcenter.voice")

# 모델 크기는 환경변수로 조정 가능 (tiny / base / small / medium / large)
# 한국어는 base 이상을 권장. CPU 추론은 int8 양자화로 빠름.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip() or "base"
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8").strip() or "int8"
WHISPER_LANG = os.getenv("WHISPER_LANG", "ko").strip() or "ko"

_model = None
_model_lock = threading.Lock()
_load_attempted = False
_load_error: str | None = None


def _try_load_model():
    """모델을 1회 로드한다 (실패 시 _load_error 에 사유 저장)."""
    global _model, _load_attempted, _load_error
    if _load_attempted:
        return _model
    _load_attempted = True
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        _load_error = "faster-whisper 패키지가 설치되어 있지 않습니다. `pip install faster-whisper` 로 설치하세요."
        logger.warning(_load_error)
        return None
    try:
        logger.info("Whisper 모델 로딩 중 (size=%s, device=%s, compute=%s)…",
                    WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE)
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        logger.info("Whisper 모델 로딩 완료.")
    except Exception as exc:
        _load_error = f"Whisper 모델 로딩 실패: {exc}"
        logger.warning(_load_error)
        _model = None
    return _model


def get_model():
    with _model_lock:
        return _try_load_model()


def is_available() -> bool:
    """모듈을 import 만으로 확인할 수 있는 가용성 (실제 로딩은 첫 호출 시)."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(path: str, language: str | None = None) -> str:
    """오디오 파일 경로를 받아 STT 결과를 단일 문자열로 반환한다."""
    model = get_model()
    if model is None:
        raise RuntimeError(_load_error or "STT 엔진을 사용할 수 없습니다.")
    segments, _info = model.transcribe(
        path,
        language=language or WHISPER_LANG,
        beam_size=1,
        vad_filter=True,  # 무음 구간 자동 제거
    )
    parts = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def last_error() -> str | None:
    return _load_error
