# 테스트용 음성 샘플

STT (음성→텍스트) 기능 검증용 한국어 음성 파일들입니다.

| 파일 | 발화 내용 | 용도 |
| --- | --- | --- |
| `greeting_ko.{wav,mp3}` | "안녕하세요. AI 콜센터입니다. 무엇을 도와드릴까요?" | 정상 인사 |
| `complaint_ko.{wav,mp3}` | "주문한 상품이 아직 도착하지 않았어요. 빨리 확인해 주세요." | 부정 감정 → 에스컬레이션 |
| `inquiry_ko.{wav,mp3}` | "환불 절차가 어떻게 되는지 알려주세요." | 일반 문의 → 답변 추천 |

## 사용 방법

### 1) 고객 채팅 UI에서 업로드
`http://localhost:8000/` → 음성 입력 버튼 → 위 파일 선택

### 2) curl 로 직접 호출
```bash
curl -F "audio=@tests/fixtures/audio/greeting_ko.wav" \
     http://localhost:8000/api/conversations/{id}/voice
```

### 3) pytest 픽스처에서 참조
```python
from pathlib import Path
AUDIO_DIR = Path(__file__).parent / "fixtures" / "audio"
sample = AUDIO_DIR / "greeting_ko.wav"
```

## 재생성

espeak-ng 기반 합성이라 실제 사람 목소리는 아니지만, Whisper STT 가
한국어 문장으로 정상 인식할 정도의 품질입니다.

```bash
sudo apt-get install -y espeak-ng ffmpeg
python tests/fixtures/audio/generate.py
```

## 더 자연스러운 샘플이 필요하면

- 실제 사람 음성을 직접 녹음 (스마트폰 메모앱 → WAV/MP3 export)
- 또는 외부망 가능 환경에서 `gTTS` (Google 무료 TTS) 사용:
  ```python
  from gtts import gTTS
  gTTS("문장", lang="ko").save("sample.mp3")
  ```
