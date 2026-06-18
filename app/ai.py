"""AI 기능 모듈.

4가지 기능을 제공한다:
  1. generate_reply     - AI 상담 챗봇 (멀티턴 자동 응답)
  2. analyze_sentiment  - 감정 분석 + 위험 상담 에스컬레이션
  3. summarize          - 상담 요약 + 카테고리/태그 분류
  4. recommend          - FAQ 지식베이스 기반 답변 추천

실제 Claude API(claude-opus-4-7)를 호출하며, ANTHROPIC_API_KEY 가 없거나
호출이 실패하면 자동으로 모의(mock) 응답으로 폴백한다.
"""
import json
import logging
import time
import urllib.error
import urllib.request

from . import config, faq

logger = logging.getLogger("ai_callcenter.ai")

_client = None  # anthropic.Anthropic 인스턴스 (지연 생성)
_ollama_cache = {"available": None, "checked_at": 0.0}  # Ollama 가용성 캐시

OLLAMA_TIMEOUT = 120  # 초 (로컬 모델 생성은 느릴 수 있음)

VALID_SENTIMENT = {"긍정", "중립", "부정", "매우 부정"}
VALID_RISK = {"low", "medium", "high"}
VALID_CATEGORY = set(faq.CATEGORIES)

# ----- 감정 분석용 키워드 (모의 응답 및 보조 신호) -----
NEGATIVE_KW = [
    "화나", "화남", "짜증", "최악", "실망", "불만", "별로", "느려", "느림",
    "안돼", "안 돼", "안됨", "고장", "항의", "거지", "끔찍", "엉망", "사기",
    "책임", "황당", "불편", "기다", "왜 이래", "환불", "취소",
]
RISK_KW = [
    "고소", "소송", "변호사", "신고", "언론", "제보", "소비자원", "갑질",
    "해지", "탈퇴", "다시는", "두 번 다시", "두번 다시", "절대", "참다참다",
]
POSITIVE_KW = [
    "감사", "고마", "좋아", "좋네", "좋습니다", "최고", "만족", "빠르", "친절",
    "훌륭", "굿", "마음에 들", "도움", "친절하",
]

CATEGORY_KW = {
    # 이커머스
    "배송": ["배송", "택배", "송장", "운송장", "도착", "발송", "물건"],
    "환불/교환": ["환불", "반품", "교환", "취소", "되돌려", "바꾸"],
    "결제": ["결제", "카드", "입금", "페이", "쿠폰", "적립", "계좌"],
    "회원/계정": ["회원가입", "회원 가입", "마이페이지", "탈퇴", "가입"],
    "제품": ["제품", "상품", "불량", "고장", "보증", "품질", "수리"],
    # IT 헬프데스크
    "계정/접속": ["로그인", "비밀번호", "비번", "잠겼", "잠김", "사번", "사내 계정", "접속"],
    "네트워크": ["vpn", "브이피엔", "네트워크", "와이파이", "wifi", "사내망", "내부망", "인트라넷"],
    "이메일": ["메일", "이메일", "스팸", "메일 용량", "수신", "발신", "메일함"],
    "소프트웨어": ["설치", "프로그램", "소프트웨어", "office", "오피스", "한글", "팀즈", "teams", "zoom"],
    "권한/보안": ["권한", "보안", "악성", "피싱", "해킹", "랜섬", "백신", "인증서", "usb"],
    "하드웨어": ["프린터", "복합기", "인쇄", "노트북", "모니터", "키보드", "마우스", "장비", "느려", "버벅"],
    "데이터": ["백업", "복원", "데이터 복구", "파일 복구", "스토리지"],
    # 한국교통안전공단(KOTSA)
    "자동차검사": ["자동차검사", "정기검사", "종합검사", "정밀검사", "검사 예약", "재검사", "검사 수수료", "검사소", "과태료"],
    "운수자격": ["자격시험", "자격증", "자격유지", "운전적성정밀검사", "운수종사자", "화물운송"],
    "운행기록·리콜": ["dtg", "디지털운행기록", "운행기록", "etas", "결함신고", "리콜"],
    "교통안전": ["교통안전 교육", "안전교육", "어린이통학버스", "통학버스"],
    "TS 누리집": ["ts 누리집", "kotsa", "공단 누리집", "공단 홈페이지", "ts 모바일", "ts 앱", "1577-0990"],
    # 악성 민원 대응 (상담원 가이드)
    "악성 민원 대응": [
        "폭언", "욕설", "협박", "위협", "악성 민원", "반복 민원", "통화 종료",
        "감정노동", "녹취", "관리 대상", "112 신고", "블랙리스트", "상담사 보호",
    ],
}

# ====================================================================
# 시스템 프롬프트
# ====================================================================

SYSTEM_CHAT = """당신은 'AI 콜센터'의 친절한 AI 상담원입니다.

답변 규칙:
- 항상 한국어 존댓말로, 정중하고 공감하는 태도로 답변합니다.
- 답변은 2~4문장으로 간결하게 작성합니다.
- 아래 FAQ 지식베이스에 근거하여 답변하고, 확실하지 않은 내용은 추측하지 않습니다.
- 환불·불만·항의 등 민감한 사안은 먼저 공감을 표현한 뒤 안내합니다.
- 고객이 강하게 화가 났거나 FAQ로 해결할 수 없는 복잡한 요청이면
  "담당 상담원에게 연결해 드리겠습니다"라고 안내합니다.
- '과거 유사 상담 사례'가 함께 제공되면, 상담원이 실제로 답변한 그 내용을
  우선 참고하여 일관된 답변을 제공합니다.

FAQ 지식베이스:
{faq}
"""

# A/B 실험용 — 변형 B: 더 간결 + 능동적 공감 + 다음 단계 명시
SYSTEM_CHAT_B = """당신은 'AI 콜센터'의 따뜻한 AI 상담원입니다.

답변 작성 방식 (이 순서를 지키세요):
1) 한 줄로 고객의 감정·상황에 먼저 공감합니다 (예: "불편하셨겠어요.").
2) 핵심 답변을 1~2문장으로 명확히 제시합니다.
3) 마지막에 '다음에 무엇을 하면 되는지' 행동 한 가지를 안내합니다.

규칙:
- 전체 답변은 3문장 이내로 짧고 명확하게.
- 한국어 존댓말, FAQ 와 과거 사례에 근거.
- 모르거나 복잡한 사안이면 즉시 '담당 상담원 연결' 안내.

FAQ 지식베이스:
{faq}
"""

SYSTEM_SENTIMENT = """당신은 고객 메시지의 감정을 분석하는 시스템입니다.
아래 JSON 형식으로만 응답하세요. JSON 외의 텍스트는 절대 출력하지 마세요.

{"sentiment": "<긍정|중립|부정|매우 부정>", "score": <-1.0~1.0 실수>, "risk_level": "<low|medium|high>", "reason": "<한 문장 설명>"}

판단 기준:
- score: 긍정일수록 1.0, 부정일수록 -1.0 에 가깝게.
- risk_level=high: 고객이 강하게 분노하거나, 법적 조치·신고·언론 제보를 언급하거나,
  해지·탈퇴 등 이탈을 위협하는 경우.
- risk_level=medium: 불만이 뚜렷하지만 위협 수준은 아닌 경우.
- risk_level=low: 그 외.
"""

SYSTEM_SUMMARY = """당신은 상담 내용을 요약하고 분류하는 시스템입니다.
아래 JSON 형식으로만 응답하세요. JSON 외의 텍스트는 절대 출력하지 마세요.

{"summary": "<상담 내용 2~3문장 요약>", "category": "<배송|환불/교환|결제|회원/계정|제품|계정/접속|네트워크|이메일|소프트웨어|권한/보안|하드웨어|데이터|자동차검사|운수자격|운행기록·리콜|교통안전|TS 누리집|악성 민원 대응|기타 중 하나>", "tags": ["<태그>", ...], "key_points": ["<핵심 사항>", ...]}

- tags: 상담을 대표하는 짧은 키워드 2~5개.
- key_points: 상담원이 알아야 할 핵심 사항 2~5개.
"""

SYSTEM_EXTRACT_FAQ = """당신은 콜센터 통화 녹취에서 FAQ 항목을 추출하는 시스템입니다.
주어진 녹취 텍스트를 읽고, 그 통화에서 다뤄진 핵심 문의 1건을 FAQ 항목으로 만들어 주세요.

다음 JSON 형식으로만 응답하세요. JSON 외의 텍스트는 절대 출력하지 마세요.
{"category": "<{categories} 중 하나>", "question": "<핵심 문의를 1문장으로>", "answer": "<상담원이 안내한 내용을 2~3문장으로 정중하게 정리>", "keywords": ["<검색용 키워드>", ...]}

- question 은 고객 입장에서의 질문으로 작성
- answer 는 상담원 응대 내용을 정중한 한국어 존댓말로 정리
- 녹취에 명시적이지 않은 내용은 추측하지 말 것
- keywords 는 2~5개의 핵심어
"""


SYSTEM_RECOMMEND = """당신은 상담원을 돕는 AI 어시스턴트입니다.
아래 FAQ 지식베이스를 참고하여, 상담원이 고객에게 바로 보낼 수 있는 추천 답변을 2~3개 제안하세요.
아래 JSON 형식으로만 응답하세요. JSON 외의 텍스트는 절대 출력하지 마세요.

{"recommendations": [{"title": "<짧은 제목>", "answer": "<고객에게 보낼 정중한 한국어 답변>", "confidence": <0.0~1.0 실수>}, ...]}

- answer: 한국어 존댓말로, 고객 상황에 맞게 FAQ 내용을 자연스럽게 다듬어 작성.
- FAQ 지식베이스에 없는 내용은 추측하지 마세요.
- '과거 유사 상담 사례'가 제공되면 상담원이 실제로 답변한 그 내용을 적극 참고하세요.

FAQ 지식베이스:
{faq}
"""


# ====================================================================
# 공개 함수
# ====================================================================

def get_ai_mode():
    """현재 동작 중인 AI 제공자: claude | ollama | mock."""
    return _resolve_provider()


def classify_category(text):
    """텍스트의 카테고리를 키워드 기반으로 추정한다 (LLM 없이 동작).

    학습된 상담 지식을 FAQ로 노출할 때 카테고리를 자동 부여하는 데 사용된다.
    """
    return _mock_category(text)


def generate_reply(history, past_cases=None, variant: str | None = None):
    """AI 상담 챗봇: 대화 이력 + 과거 학습 사례 기반 멀티턴 응답.

    history:    [{"role": "customer|ai|agent", "content": str}, ...]
    past_cases: [{"question": str, "answer": str}, ...] — 학습된 과거 상담 사례
    variant:    'A' (기본) 또는 'B' (실험 — 공감 → 답변 → 다음 행동 구조)
    반환: {"reply": str, "source": "claude"|"ollama"|"mock", "variant": "A"|"B"}
    """
    provider = _resolve_provider()
    use_b = variant == "B"
    base = SYSTEM_CHAT_B if use_b else SYSTEM_CHAT
    label = "B" if use_b else "A"
    if provider != "mock" and history:
        try:
            system = base.replace("{faq}", faq.as_prompt_text())
            system += _past_cases_block(past_cases)
            text = _complete(provider, system, _to_api_messages(history), max_tokens=700)
            if text:
                return {"reply": text, "source": provider, "variant": label}
        except Exception as exc:
            logger.warning("generate_reply: 모의 응답으로 폴백 (%s)", exc)
    return {"reply": _mock_reply(history, past_cases), "source": "mock", "variant": label}


def analyze_sentiment(text):
    """감정 분석: 단일 고객 메시지의 감정·위험도 평가.

    반환: {"sentiment", "score", "risk_level", "reason", "source"}
    """
    provider = _resolve_provider()
    if provider != "mock" and text.strip():
        try:
            raw = _complete(
                provider,
                SYSTEM_SENTIMENT,
                [{"role": "user", "content": text}],
                max_tokens=120,
                want_json=True,
            )
            return _normalize_sentiment(_extract_json(raw), source=provider)
        except Exception as exc:
            logger.warning("analyze_sentiment: 모의 응답으로 폴백 (%s)", exc)
    return _mock_sentiment(text)


def summarize(history):
    """상담 요약·분류: 전체 대화를 요약하고 카테고리/태그를 부여한다.

    반환: {"summary", "category", "tags", "key_points", "source"}
    """
    provider = _resolve_provider()
    if provider != "mock" and history:
        try:
            raw = _complete(
                provider,
                SYSTEM_SUMMARY,
                [{"role": "user", "content": _transcript(history)}],
                max_tokens=800,
                want_json=True,
            )
            return _normalize_summary(_extract_json(raw), source=provider)
        except Exception as exc:
            logger.warning("summarize: 모의 응답으로 폴백 (%s)", exc)
    return _mock_summary(history)


def extract_faq_from_transcript(transcript):
    """녹취 텍스트에서 FAQ 후보(카테고리·질문·답변·키워드)를 추출한다.

    반환: {"category", "question", "answer", "keywords", "source"}
    """
    provider = _resolve_provider()
    if provider != "mock" and transcript.strip():
        try:
            raw = _complete(
                provider,
                SYSTEM_EXTRACT_FAQ.replace("{categories}", "|".join(faq.CATEGORIES)),
                [{"role": "user", "content": transcript.strip()[:6000]}],
                max_tokens=700,
                want_json=True,
            )
            data = _extract_json(raw)
            return _normalize_extracted_faq(data, source=provider)
        except Exception as exc:
            logger.warning("extract_faq_from_transcript: 모의 응답으로 폴백 (%s)", exc)
    return _mock_extract_faq(transcript)


def _normalize_extracted_faq(data, source):
    category = str(data.get("category", "기타")).strip()
    if category not in VALID_CATEGORY:
        category = "기타"
    question = str(data.get("question", "")).strip() or "(질문 미상)"
    answer = str(data.get("answer", "")).strip() or "(답변 미상)"
    kws = data.get("keywords", [])
    if not isinstance(kws, list):
        kws = []
    kws = [str(k).strip() for k in kws if str(k).strip()][:8]
    return {
        "category": category,
        "question": question,
        "answer": answer,
        "keywords": kws,
        "source": source,
    }


def _mock_extract_faq(transcript):
    """LLM 없이 휴리스틱으로 FAQ 후보를 만든다."""
    text = transcript.strip()
    snippet = text[:200] if text else ""
    category = _mock_category(text)
    # 첫 물음표 직전을 질문 후보로 사용
    qmark = text.find("?")
    question = (text[: qmark + 1].strip() if qmark != -1 else snippet[:80]) or "(질문 미상)"
    answer = text[qmark + 1:].strip()[:400] if qmark != -1 and len(text) > qmark + 1 else snippet
    matched = faq.search(text, limit=2)
    keywords = [it["category"] for it in matched] or [category]
    return {
        "category": category,
        "question": question,
        "answer": answer or "(답변 미상)",
        "keywords": list(dict.fromkeys(keywords))[:6],
        "source": "mock",
    }


def recommend(history, past_cases=None):
    """상담원 답변 추천: FAQ 지식베이스 + 과거 학습 사례 기반 추천 답변 목록.

    반환: {"recommendations": [{"title", "answer", "confidence"}], "source"}
    """
    provider = _resolve_provider()
    if provider != "mock" and history:
        try:
            system = SYSTEM_RECOMMEND.replace("{faq}", faq.as_prompt_text())
            system += _past_cases_block(past_cases)
            raw = _complete(
                provider,
                system,
                [{"role": "user", "content": _transcript(history)}],
                max_tokens=900,
                want_json=True,
            )
            recs = _normalize_recommendations(_extract_json(raw))
            if recs:
                return {"recommendations": recs, "source": provider}
        except Exception as exc:
            logger.warning("recommend: 모의 응답으로 폴백 (%s)", exc)
    return _mock_recommend(history, past_cases)


# ====================================================================
# AI 제공자 (Claude / Ollama) 호출
# ====================================================================

def _resolve_provider():
    """현재 사용할 AI 제공자를 결정한다: claude | ollama | mock.

    config.AI_PROVIDER 설정을 따른다.
      claude - ANTHROPIC_API_KEY 가 있으면 Claude, 없으면 mock
      ollama - Ollama 서버가 응답하면 Ollama, 아니면 mock
      mock   - 항상 모의 응답
      auto   - Claude 키가 있으면 Claude, 없으면 Ollama, 둘 다 없으면 mock
    """
    provider = config.AI_PROVIDER
    if provider == "mock":
        return "mock"
    if provider == "claude":
        return "claude" if config.ANTHROPIC_API_KEY else "mock"
    if provider == "ollama":
        return "ollama" if _ollama_available() else "mock"
    # auto
    if config.ANTHROPIC_API_KEY:
        return "claude"
    return "ollama" if _ollama_available() else "mock"


def _ollama_available():
    """Ollama 서버가 응답하는지 확인한다 (결과를 30초간 캐시)."""
    now = time.time()
    if _ollama_cache["available"] is not None and now - _ollama_cache["checked_at"] < 30:
        return _ollama_cache["available"]
    available = False
    try:
        url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=2) as resp:
            available = 200 <= resp.status < 300
    except Exception:
        available = False
    _ollama_cache["available"] = available
    _ollama_cache["checked_at"] = now
    return available


def _complete(provider, system, messages, max_tokens, want_json=False):
    """선택된 제공자로 텍스트 응답을 생성한다."""
    if provider == "ollama":
        return _complete_ollama(system, messages, max_tokens, want_json)
    return _complete_claude(system, messages, max_tokens)


def _complete_claude(system, messages, max_tokens):
    """Claude Messages API 호출.

    시스템 프롬프트(FAQ 포함, 요청 간 안정적인 prefix)에 prompt caching을 적용한다.
    """
    client = _client_or_none()
    if client is None:
        raise RuntimeError("Claude 클라이언트를 사용할 수 없습니다.")
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _complete_ollama(system, messages, max_tokens, want_json):
    """로컬 Ollama 서버의 /api/chat 엔드포인트를 호출한다."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if want_json:
        payload["format"] = "json"  # Ollama가 유효한 JSON만 생성하도록 강제
    request = urllib.request.Request(
        config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as resp:
        body = json.loads(resp.read())
    return (body.get("message", {}).get("content") or "").strip()


def embeddings_available():
    """의미 기반 검색(임베딩)을 사용할 수 있는지 여부. Ollama 가용 시 True."""
    return _ollama_available()


def embed_text(text):
    """텍스트를 임베딩 벡터로 변환한다 (Ollama). 불가능하면 None 을 반환.

    상담 학습(RAG)의 의미 기반 검색에 사용된다. 신규 /api/embed
    엔드포인트를 먼저 시도하고, 구버전 Ollama 의 경우 404 응답에
    한해 레거시 /api/embeddings 로 폴백한다.
    """
    if not text or not text.strip() or not _ollama_available():
        return None
    base = config.OLLAMA_BASE_URL.rstrip("/")
    model = config.OLLAMA_EMBED_MODEL

    # 1) 모던 엔드포인트: /api/embed  (요청: {model,input}, 응답: {embeddings:[[...]]})
    try:
        request = urllib.request.Request(
            base + "/api/embed",
            data=json.dumps({"model": model, "input": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as resp:
            body = json.loads(resp.read())
        # 신규 응답 형식: {"embeddings": [[...]]}
        embeddings = body.get("embeddings")
        if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
            return [float(x) for x in embeddings[0]]
        # 일부 구현은 단일 형태 반환
        if isinstance(body.get("embedding"), list):
            return [float(x) for x in body["embedding"]]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            logger.warning("embed_text: /api/embed 실패 (%s)", exc)
            return None
        # 404 면 레거시 엔드포인트 시도
    except Exception as exc:
        logger.warning("embed_text: /api/embed 실패 (%s)", exc)
        return None

    # 2) 레거시 엔드포인트: /api/embeddings  (요청: {model,prompt}, 응답: {embedding:[...]})
    try:
        request = urllib.request.Request(
            base + "/api/embeddings",
            data=json.dumps({"model": model, "prompt": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as resp:
            body = json.loads(resp.read())
        embedding = body.get("embedding")
        if isinstance(embedding, list) and embedding:
            return [float(x) for x in embedding]
    except Exception as exc:
        logger.warning("embed_text: /api/embeddings 폴백 실패 (%s)", exc)
    return None


def _client_or_none():
    """API 키가 있으면 anthropic 클라이언트를, 없으면 None을 반환."""
    global _client
    if not config.ANTHROPIC_API_KEY:
        return None
    if _client is None:
        try:
            import anthropic
        except ImportError:
            logger.warning("anthropic 패키지가 설치되어 있지 않습니다.")
            return None
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _to_api_messages(history):
    """내부 대화 이력을 Claude messages 형식으로 변환한다.

    customer -> user, ai/agent -> assistant. 연속된 동일 역할은 합치고,
    첫 메시지가 user가 되도록 보정한다.
    """
    messages = []
    for item in history:
        role = "user" if item["role"] == "customer" else "assistant"
        content = item["content"]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + content
        else:
            messages.append({"role": role, "content": content})
    if not messages or messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "(상담을 시작합니다)"})
    return messages


def _transcript(history):
    """대화 이력을 사람이 읽는 텍스트로 변환한다."""
    label = {"customer": "고객", "ai": "AI상담봇", "agent": "상담원"}
    return "\n".join(
        f"{label.get(item['role'], item['role'])}: {item['content']}" for item in history
    )


def _past_cases_block(past_cases):
    """학습된 과거 상담 사례를 시스템 프롬프트에 덧붙일 텍스트로 변환한다."""
    if not past_cases:
        return ""
    lines = ["", "과거 유사 상담 사례 (상담원이 실제로 답변한 내용 — 우선 참고):"]
    for case in past_cases:
        lines.append(f"- 고객 문의: {case['question']}")
        lines.append(f"  상담원 답변: {case['answer']}")
    return "\n".join(lines)


def _extract_json(text):
    """모델 응답에서 JSON 객체를 추출해 파싱한다."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("응답에서 JSON을 찾을 수 없습니다.")
    return json.loads(text[start:end + 1])


# ====================================================================
# 응답 정규화
# ====================================================================

def _normalize_sentiment(data, source):
    sentiment = str(data.get("sentiment", "중립")).strip()
    if sentiment not in VALID_SENTIMENT:
        sentiment = "중립"
    try:
        score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(-1.0, min(1.0, score))
    risk = str(data.get("risk_level", "low")).strip().lower()
    if risk not in VALID_RISK:
        risk = "low"
    reason = str(data.get("reason", "")).strip() or "분석 결과입니다."
    return {
        "sentiment": sentiment,
        "score": round(score, 2),
        "risk_level": risk,
        "reason": reason,
        "source": source,
    }


def _normalize_summary(data, source):
    summary = str(data.get("summary", "")).strip() or "요약 정보가 없습니다."
    category = str(data.get("category", "기타")).strip()
    if category not in VALID_CATEGORY:
        category = "기타"
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()][:6]
    key_points = data.get("key_points", [])
    if not isinstance(key_points, list):
        key_points = []
    key_points = [str(k).strip() for k in key_points if str(k).strip()][:8]
    return {
        "summary": summary,
        "category": category,
        "tags": tags,
        "key_points": key_points,
        "source": source,
    }


def _normalize_recommendations(data):
    recs = data.get("recommendations", []) if isinstance(data, dict) else []
    result = []
    for item in recs:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer", "")).strip()
        if not answer:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        result.append({
            "title": str(item.get("title", "추천 답변")).strip() or "추천 답변",
            "answer": answer,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
        })
    return result[:4]


# ====================================================================
# 모의(mock) 응답 — API 키가 없거나 호출 실패 시 사용
# ====================================================================

def _count(text, words):
    return sum(1 for w in words if w in text)


def _mock_reply(history, past_cases=None):
    last = next((m["content"] for m in reversed(history) if m["role"] == "customer"), "")
    lowered = last.lower()
    if _count(lowered, RISK_KW) >= 1 or _count(lowered, NEGATIVE_KW) >= 2:
        return (
            "불편을 드려 진심으로 죄송합니다. 고객님의 상황을 빠르고 정확하게 "
            "처리해 드릴 수 있도록 담당 상담원에게 연결해 드리겠습니다. 잠시만 기다려 주세요."
        )
    if past_cases:  # 학습된 과거 상담 사례 우선
        return (
            "문의해 주셔서 감사합니다. 이전 유사 상담 사례를 참고해 안내드립니다. "
            f"{past_cases[0]['answer']} 더 궁금하신 점이 있으시면 말씀해 주세요."
        )
    matches = faq.search(last, limit=1)
    if matches:
        return f"문의해 주셔서 감사합니다. {matches[0]['answer']} 더 궁금하신 점이 있으시면 말씀해 주세요."
    return (
        "문의해 주셔서 감사합니다. 보다 정확히 안내해 드릴 수 있도록 문의 내용을 "
        "조금 더 자세히 말씀해 주시거나, 원하시면 담당 상담원에게 연결해 드리겠습니다."
    )


def _mock_sentiment(text):
    lowered = (text or "").lower()
    neg = _count(lowered, NEGATIVE_KW)
    pos = _count(lowered, POSITIVE_KW)
    risk = _count(lowered, RISK_KW)
    if risk >= 1 or neg >= 3:
        return _normalize_sentiment(
            {"sentiment": "매우 부정", "score": -0.85, "risk_level": "high",
             "reason": "강한 불만 또는 이탈·법적 조치 가능성이 감지되었습니다."},
            source="mock",
        )
    if neg >= 1 and neg >= pos:
        return _normalize_sentiment(
            {"sentiment": "부정", "score": -0.5, "risk_level": "medium",
             "reason": "부정적이거나 불만이 담긴 표현이 감지되었습니다."},
            source="mock",
        )
    if pos >= 1 and pos > neg:
        return _normalize_sentiment(
            {"sentiment": "긍정", "score": 0.6, "risk_level": "low",
             "reason": "긍정적인 표현이 감지되었습니다."},
            source="mock",
        )
    return _normalize_sentiment(
        {"sentiment": "중립", "score": 0.0, "risk_level": "low",
         "reason": "뚜렷한 감정 신호가 없습니다."},
        source="mock",
    )


def _mock_category(text):
    lowered = (text or "").lower()
    best, best_score = "기타", 0
    for category, keywords in CATEGORY_KW.items():
        score = _count(lowered, keywords)
        if score > best_score:
            best, best_score = category, score
    return best


def _mock_summary(history):
    customer_msgs = [m["content"] for m in history if m["role"] == "customer"]
    joined = " ".join(customer_msgs)
    if not customer_msgs:
        summary = "아직 고객 문의 내용이 없습니다."
    elif len(customer_msgs) == 1:
        summary = f"고객 문의: {customer_msgs[0][:140]}"
    else:
        summary = (
            f"고객이 총 {len(customer_msgs)}건의 메시지를 보냈습니다. "
            f"주요 문의: {customer_msgs[0][:100]}"
        )
    category = _mock_category(joined)
    tags = [it["category"] for it in faq.search(joined, limit=3)]
    tags = list(dict.fromkeys(tags)) or [category]
    key_points = [c[:90] for c in customer_msgs[:4]]
    return {
        "summary": summary,
        "category": category,
        "tags": tags,
        "key_points": key_points,
        "source": "mock",
    }


def _mock_recommend(history, past_cases=None):
    recs = []
    for case in (past_cases or [])[:2]:  # 학습된 과거 상담 사례 우선
        recs.append({
            "title": "과거 상담 사례 기반",
            "answer": case["answer"],
            "confidence": 0.78,
        })
    text = " ".join(m["content"] for m in history if m["role"] == "customer")
    for item in faq.search(text, limit=3):
        recs.append({
            "title": item["question"],
            "answer": f"고객님, 문의해 주셔서 감사합니다. {item['answer']}",
            "confidence": 0.7,
        })
    if not recs:
        recs.append({
            "title": "상담원 직접 안내",
            "answer": (
                "고객님, 문의해 주셔서 감사합니다. 문의하신 내용을 확인한 뒤 "
                "정확하게 안내해 드리겠습니다. 잠시만 기다려 주세요."
            ),
            "confidence": 0.3,
        })
    return {"recommendations": recs[:4], "source": "mock"}
