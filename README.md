# AI 콜센터

FastAPI + SQLite 기반의 독립 실행형 AI 콜센터 애플리케이션입니다.
AI 제공자로 **Claude API**(`claude-opus-4-7`) 또는 **로컬 Ollama** 를 선택할 수
있으며, 둘 다 사용할 수 없으면 자동으로 모의(mock) 응답으로 동작하여 키 없이도
전체 기능을 체험할 수 있습니다.

## AI 기능 4종

| 기능 | 설명 | 엔드포인트 |
| --- | --- | --- |
| AI 상담 챗봇 | 고객 문의 자동 응답 (멀티턴 대화 + 과거 상담 학습) | `POST /api/conversations/{id}/chat` |
| 상담 요약·분류 | 통화/채팅 내용 자동 요약 + 카테고리·태그 분류 | `POST /api/conversations/{id}/analyze` |
| 감정 분석 | 고객 메시지 감정 분석 + 위험 상담 자동 에스컬레이션 | 채팅 시 자동 실행 |
| 답변 추천 | FAQ + 학습된 과거 상담 사례 기반 상담원 추천 답변 | `POST /api/conversations/{id}/recommend` |

추가로 **상담 학습(RAG)** 기능이 챗봇·답변 추천에 적용됩니다 — 아래 참고.

## UI 2종

- **고객 채팅 화면** — `http://localhost:8000/` (텍스트 + 음성 상담, 모바일 대응)
- **상담원 콘솔** — `http://localhost:8000/agent` (상담 목록, AI 분석/추천, 답변 전송)

## 실행 방법

```bash
pip install -r requirements.txt

# (선택) 실제 Claude API 사용 시
cp .env.example .env
# .env 파일에 ANTHROPIC_API_KEY 입력

python run.py
```

브라우저에서 접속:

- 고객 채팅: <http://localhost:8000/>
- 상담원 콘솔: <http://localhost:8000/agent>

## 동작 모드 (AI 제공자)

`.env` 의 `AI_PROVIDER` 로 AI 제공자를 선택합니다.

| `AI_PROVIDER` | 동작 |
| --- | --- |
| `auto` (기본값) | Claude 키가 있으면 Claude, 없으면 Ollama, 둘 다 없으면 mock |
| `claude` | 실제 Claude API (`claude-opus-4-7`) |
| `ollama` | 로컬 Ollama 서버 |
| `mock` | 모의 응답만 사용 |

- 어떤 제공자를 쓰더라도 호출이 실패하면 **모의(mock) 응답**으로 자동 폴백합니다.
- 현재 제공자는 각 화면 상단 배지에서 확인할 수 있습니다 (`Claude 연결됨` / `Ollama 연결됨` / `데모 모드`).

### Ollama 사용 방법

1. [Ollama](https://ollama.com) 설치 후 모델을 받습니다. 예) `ollama pull llama3.1`
2. `.env` 에 다음을 설정합니다.
   ```
   AI_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.1
   ```
3. Ollama 서버가 떠 있으면(`ollama serve`) 앱이 자동으로 연동됩니다.

> 구조화 출력이 필요한 기능(감정 분석·요약·추천)은 Ollama 의 `format: "json"` 옵션을
> 사용해 유효한 JSON 응답을 받습니다.

## 상담 학습 (RAG)

과거 상담 내용을 학습하여 다음 답변에 반영합니다.

1. **학습** — 상담을 `종료(closed)` 상태로 바꾸면, 그 상담의 `고객 문의 → 상담원 답변`
   쌍이 지식으로 저장됩니다. 상담원이 실제로 답변한 내용만 학습 대상입니다.
2. **검색** — 새 문의가 들어오면 의미가 유사한 과거 사례를 찾아, AI 챗봇 응답과
   상담원 답변 추천에 함께 활용합니다.
3. **검색 방식**
   - **의미 검색(임베딩)** — Ollama 가 있으면 `nomic-embed-text` 로 임베딩해
     코사인 유사도로 검색합니다 (`ollama pull nomic-embed-text` 필요).
   - **키워드 검색** — 임베딩을 쓸 수 없으면 단어 겹침 기반으로 자동 폴백합니다.

상담원 콘솔 상단 배지에서 학습된 사례 수와 현재 검색 방식을 확인할 수 있고,
답변 추천 시 참고한 과거 상담이 함께 표시됩니다.

## 음성 상담 (모바일)

고객 채팅 화면은 브라우저 **Web Speech API** 로 음성 상담을 지원합니다.

- **음성 입력(STT)** — 입력창 옆 마이크 버튼을 누르고 말하면 텍스트로 변환되어
  자동 전송됩니다 (`ko-KR`).
- **음성 재생(TTS)** — AI·상담원의 답변을 음성으로 읽어줍니다. 헤더의 스피커
  버튼으로 켜고 끌 수 있습니다.
- 별도 서버나 API 키가 필요 없으며, 작은 화면에서는 전체 화면으로 동작합니다.
- 음성 인식을 지원하지 않는 브라우저(예: 일부 iOS Safari)에서는 마이크 버튼이
  자동으로 숨겨지고 텍스트 입력으로 이용할 수 있습니다.

## 비밀번호 재설정 지원

상담원이 고객의 비밀번호 재설정을 도와주는 기능입니다.

1. **계정 확인** — 상담원 콘솔의 '비밀번호 재설정 지원' 카드에서 고객의
   이메일 또는 아이디를 입력하면, 대상 시스템 계정 테이블에서 사용자를
   조회해 마스킹된 정보를 보여줍니다.
2. **재설정 링크 발송** — '재설정 링크 발송'을 누르면 만료 시간(30분)이 있는
   1회용 토큰이 생성되어, SMTP가 설정돼 있으면 이메일로 발송되고 없으면
   서버 로그로 출력됩니다(개발용 폴백 — 이때는 콘솔에 링크가 직접 표시됨).
3. **비밀번호 변경** — 고객이 링크(`/reset?token=...`)에 접속해 새 비밀번호를
   설정합니다. 토큰은 1회용이며 사용 후 무효화됩니다.

비밀번호는 PBKDF2-SHA256 으로 해시 저장됩니다. 데모용 계정이 자동 생성됩니다
(`minseo@example.com`, `junho@example.com`, `jiyoung@example.com` / 초기 비밀번호 `password1234`).

> 데모 앱이라 상담원 콘솔과 계정 확인 API에는 인증이 없습니다. 실제 배포
> 시에는 콘솔과 해당 API를 상담원 인증으로 보호해야 합니다.

## 프로젝트 구조

```
ai-callcenter/
├── app/
│   ├── main.py        # FastAPI 진입점, 화면 라우팅
│   ├── config.py      # 환경 설정 (API 키, 모델, DB)
│   ├── database.py    # SQLite + SQLAlchemy
│   ├── models.py      # Conversation / Message 모델
│   ├── schemas.py     # 요청 스키마
│   ├── ai.py          # Claude/Ollama 연동 + 모의 응답 폴백 (AI 기능 4종, 임베딩)
│   ├── faq.py         # FAQ 지식베이스
│   ├── knowledge.py   # 상담 학습(RAG): 학습·임베딩·유사 사례 검색
│   ├── accounts.py    # 대상 시스템 계정 + 비밀번호 재설정 (해시·토큰·메일)
│   ├── service.py     # 직렬화·조회 헬퍼
│   └── routers/
│       ├── chat.py    # 고객 채팅 API
│       ├── agent.py   # 상담원 콘솔 API
│       └── password.py # 비밀번호 재설정 지원 API
├── static/
│   ├── customer.html  # 고객 채팅 화면
│   ├── agent.html     # 상담원 콘솔 화면
│   └── reset.html     # 비밀번호 재설정 화면
├── requirements.txt
└── run.py
```

## 기술 메모

- AI 호출은 `app/ai.py` 한 곳에 집중되어 있으며, 실패 시 예외를 잡아 모의 응답으로 폴백합니다.
- 시스템 프롬프트(FAQ 포함, 요청 간 안정적인 prefix)에 prompt caching을 적용했습니다.
- 감정 분석에서 위험도(`high`)가 감지되면 해당 상담이 자동으로 에스컬레이션 상태로 전환됩니다.
- 고객/상담원 화면은 주기적 폴링으로 서로의 메시지를 반영합니다.
