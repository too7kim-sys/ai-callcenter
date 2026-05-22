# AI 콜센터

FastAPI + SQLite 기반의 독립 실행형 AI 콜센터 애플리케이션입니다.
실제 Claude API(`claude-opus-4-7`)와 연동되며, API 키가 없으면 자동으로
모의(mock) 응답으로 동작하여 키 없이도 전체 기능을 체험할 수 있습니다.

## AI 기능 4종

| 기능 | 설명 | 엔드포인트 |
| --- | --- | --- |
| AI 상담 챗봇 | 고객 문의 자동 응답 (멀티턴 대화) | `POST /api/conversations/{id}/chat` |
| 상담 요약·분류 | 통화/채팅 내용 자동 요약 + 카테고리·태그 분류 | `POST /api/conversations/{id}/analyze` |
| 감정 분석 | 고객 메시지 감정 분석 + 위험 상담 자동 에스컬레이션 | 채팅 시 자동 실행 |
| 답변 추천 | FAQ 지식베이스 기반 상담원 추천 답변 | `POST /api/conversations/{id}/recommend` |

## UI 2종

- **고객 채팅 화면** — `http://localhost:8000/`
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

## 동작 모드

- `ANTHROPIC_API_KEY` 가 설정되어 있으면 **실제 Claude API**(`claude-opus-4-7`)를 호출합니다.
- 키가 없거나 API 호출이 실패하면 **모의(mock) 응답**으로 자동 폴백합니다.
- 현재 모드는 각 화면 상단 배지에서 확인할 수 있습니다 (`AI 연결됨` / `데모 모드`).

## 프로젝트 구조

```
ai-callcenter/
├── app/
│   ├── main.py        # FastAPI 진입점, 화면 라우팅
│   ├── config.py      # 환경 설정 (API 키, 모델, DB)
│   ├── database.py    # SQLite + SQLAlchemy
│   ├── models.py      # Conversation / Message 모델
│   ├── schemas.py     # 요청 스키마
│   ├── ai.py          # Claude API 연동 + 모의 응답 폴백 (AI 기능 4종)
│   ├── faq.py         # FAQ 지식베이스
│   ├── service.py     # 직렬화·조회 헬퍼
│   └── routers/
│       ├── chat.py    # 고객 채팅 API
│       └── agent.py   # 상담원 콘솔 API
├── static/
│   ├── customer.html  # 고객 채팅 화면
│   └── agent.html     # 상담원 콘솔 화면
├── requirements.txt
└── run.py
```

## 기술 메모

- AI 호출은 `app/ai.py` 한 곳에 집중되어 있으며, 실패 시 예외를 잡아 모의 응답으로 폴백합니다.
- 시스템 프롬프트(FAQ 포함, 요청 간 안정적인 prefix)에 prompt caching을 적용했습니다.
- 감정 분석에서 위험도(`high`)가 감지되면 해당 상담이 자동으로 에스컬레이션 상태로 전환됩니다.
- 고객/상담원 화면은 주기적 폴링으로 서로의 메시지를 반영합니다.
