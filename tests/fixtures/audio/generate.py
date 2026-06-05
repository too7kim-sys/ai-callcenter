"""STT(음성→텍스트) 테스트용 한국어 음성 샘플 생성 스크립트.

오프라인 TTS(espeak-ng)로 한국어 WAV/MP3 를 만든다.
실제 사람 음성이 아니므로 음질은 낮지만, Whisper STT 동작 검증과
업로드/디코딩/파이프라인 테스트에는 충분하다.

사용법:
    # 사전 설치 (Ubuntu/Debian)
    sudo apt-get install -y espeak-ng ffmpeg

    python tests/fixtures/audio/generate.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SAMPLES: dict[str, str] = {
    "greeting_ko":  "안녕하세요. AI 콜센터입니다. 무엇을 도와드릴까요?",
    "complaint_ko": "주문한 상품이 아직 도착하지 않았어요. 빨리 확인해 주세요.",
    "inquiry_ko":   "환불 절차가 어떻게 되는지 알려주세요.",
}

OUT_DIR = Path(__file__).resolve().parent


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        sys.exit(f"'{binary}' 가 PATH 에 없습니다. 먼저 설치해 주세요.")
    return path


def main() -> None:
    espeak = _require("espeak-ng")
    ffmpeg = _require("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, text in SAMPLES.items():
        wav = OUT_DIR / f"{name}.wav"
        mp3 = OUT_DIR / f"{name}.mp3"
        subprocess.run(
            [espeak, "-v", "ko", "-s", "150", text, "-w", str(wav)],
            check=True,
        )
        subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(wav), str(mp3)],
            check=True,
        )
        print(f"  ✓ {wav.name} / {mp3.name}  — \"{text}\"")


if __name__ == "__main__":
    main()
