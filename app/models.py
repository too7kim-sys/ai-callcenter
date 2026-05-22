"""DB 모델: 상담(Conversation)과 메시지(Message)."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def _now():
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, default="고객")
    channel = Column(String, default="chat")  # chat / call
    status = Column(String, default="open")   # open / escalated / closed

    # 상담 요약·분류 결과
    category = Column(String, nullable=True)
    tags = Column(Text, nullable=True)         # JSON 배열 문자열
    summary = Column(Text, nullable=True)
    key_points = Column(Text, nullable=True)   # JSON 배열 문자열

    # 감정 분석 결과 (최신 고객 메시지 기준)
    sentiment = Column(String, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    risk_level = Column(String, nullable=True)  # low / medium / high

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    messages = relationship(
        "Message",
        back_populates="conversation",
        order_by="Message.id",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    role = Column(String)  # customer / ai / agent
    content = Column(Text)

    # 고객 메시지에 대한 감정 분석 결과
    sentiment = Column(String, nullable=True)
    sentiment_score = Column(Float, nullable=True)

    created_at = Column(DateTime, default=_now)

    conversation = relationship("Conversation", back_populates="messages")


class KnowledgeItem(Base):
    """학습된 상담 지식. 종료된 상담의 (고객 문의 → 상담원 답변) 쌍을 저장한다."""

    __tablename__ = "knowledge_items"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    question = Column(Text)   # 고객 문의
    answer = Column(Text)     # 상담원이 실제로 보낸 답변
    embedding = Column(Text, nullable=True)  # 질문 임베딩 벡터 (JSON 배열). 없으면 키워드 검색.
    created_at = Column(DateTime, default=_now)
