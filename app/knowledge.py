"""상담 학습 모듈 (RAG).

종료된 상담에서 (고객 문의 → 상담원 답변) 쌍을 추출해 지식으로 저장하고,
새 문의가 들어오면 의미가 유사한 과거 사례를 검색해 답변에 활용한다.

검색은 임베딩(의미 기반)이 가능하면 이를 사용하고, 불가능하면
키워드(단어 겹침) 기반으로 자동 폴백한다.
"""
import json
import math
import re

from . import ai
from .models import KnowledgeItem

EMBEDDING_MIN_SCORE = 0.5   # 코사인 유사도 최소 임계값
KEYWORD_MIN_SCORE = 1       # 키워드 겹침 최소 개수

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def learn_from_conversation(db, conv):
    """상담에서 (고객 문의 → 상담원 답변) 쌍을 추출해 지식으로 저장한다.

    상담원이 실제로 답변한 사례만 학습 대상으로 삼는다.
    재실행 시 해당 상담의 기존 학습 항목을 교체한다.
    반환: 학습한 항목 수.
    """
    db.query(KnowledgeItem).filter(
        KnowledgeItem.conversation_id == conv.id
    ).delete(synchronize_session=False)

    pairs = _extract_pairs(conv)
    for question, answer in pairs:
        embedding = ai.embed_text(question)
        db.add(KnowledgeItem(
            conversation_id=conv.id,
            question=question,
            answer=answer,
            embedding=json.dumps(embedding) if embedding else None,
        ))
    db.commit()
    return len(pairs)


def retrieve(db, query, limit=3):
    """질의와 유사한 과거 상담 사례를 반환한다.

    반환: [{"question", "answer", "score", "method", "conversation_id"}, ...]
          method 는 "embedding"(의미 검색) 또는 "keyword"(키워드 검색).
    """
    if not query or not query.strip():
        return []
    items = db.query(KnowledgeItem).all()
    if not items:
        return []

    scored = []
    method = "keyword"

    query_embedding = ai.embed_text(query)
    if query_embedding:
        for item in items:
            if not item.embedding:
                continue
            score = _cosine(query_embedding, json.loads(item.embedding))
            if score >= EMBEDDING_MIN_SCORE:
                scored.append((score, item))
        if scored:
            method = "embedding"

    if not scored:  # 키워드 기반 폴백
        method = "keyword"
        for item in items:
            score = _keyword_score(query, item.question)
            if score >= KEYWORD_MIN_SCORE:
                scored.append((float(score), item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "question": item.question,
            "answer": item.answer,
            "score": round(score, 3),
            "method": method,
            "conversation_id": item.conversation_id,
        }
        for score, item in scored[:limit]
    ]


def count(db):
    """학습된 지식 항목 수."""
    return db.query(KnowledgeItem).count()


# --------------------------------------------------------------------

def _extract_pairs(conv):
    """대화에서 (고객 문의 → 바로 뒤 상담원 답변) 쌍을 추출한다."""
    pairs = []
    pending = []  # 아직 답변되지 않은 고객 메시지
    for message in conv.messages:
        if message.role == "customer":
            pending.append(message.content)
        elif message.role == "agent" and pending:
            pairs.append(("\n".join(pending), message.content))
            pending = []
    return pairs


def _cosine(vec_a, vec_b):
    if len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_score(query, text):
    """질의와 텍스트의 단어 겹침 개수 (임베딩 사용 불가 시 폴백)."""
    query_tokens = {t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= 2}
    target = text.lower()
    return sum(1 for token in query_tokens if token in target)
