"""DB 모델: 상담(Conversation)과 메시지(Message)."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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

    # 상담원 배정 / 고객 요청 / 만족도
    assigned_agent_id = Column(Integer, ForeignKey("agent_users.id"), nullable=True, index=True)
    agent_requested = Column(Boolean, default=False)  # 고객이 명시적 상담원 연결 요청
    customer_rating = Column(Integer, nullable=True)  # 1~5
    customer_feedback = Column(Text, nullable=True)

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

    # 고객의 메시지 평가 (AI·상담원 답변에만 의미 있음): up / down / null
    feedback = Column(String, nullable=True)

    created_at = Column(DateTime, default=_now)

    conversation = relationship("Conversation", back_populates="messages")


class ReplyTemplate(Base):
    """상담원이 자주 쓰는 답변 템플릿."""

    __tablename__ = "reply_templates"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    content = Column(Text)
    category = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("agent_users.id"), nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ConversationNote(Base):
    """상담원만 보는 내부 메모 (고객에게는 노출 안 함)."""

    __tablename__ = "conversation_notes"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    author_id = Column(Integer, ForeignKey("agent_users.id"), nullable=True)
    content = Column(Text)
    created_at = Column(DateTime, default=_now)


class AuditLog(Base):
    """관리자 행위 변경 이력."""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("agent_users.id"), nullable=True, index=True)
    actor_name = Column(String, nullable=True)  # 비정규화 — 사용자 삭제 후에도 보존
    action = Column(String, index=True)         # 예: user.create, faq.delete, role.update
    target_type = Column(String, nullable=True) # user / faq / role / conversation / ...
    target_id = Column(String, nullable=True)   # 문자열 (학습 항목은 'L1' 등)
    details = Column(Text, nullable=True)       # JSON 문자열 (자유 메타데이터)
    created_at = Column(DateTime, default=_now, index=True)


class KnowledgeItem(Base):
    """학습된 상담 지식. 종료된 상담의 (고객 문의 → 상담원 답변) 쌍을 저장한다."""

    __tablename__ = "knowledge_items"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    question = Column(Text)   # 고객 문의
    answer = Column(Text)     # 상담원이 실제로 보낸 답변
    embedding = Column(Text, nullable=True)  # 질문 임베딩 벡터 (JSON 배열). 없으면 키워드 검색.
    created_at = Column(DateTime, default=_now)


class Account(Base):
    """대상 시스템(콜센터가 지원하는 서비스)의 사용자 계정."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    phone = Column(String, nullable=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=_now)


class PasswordResetToken(Base):
    """비밀번호 재설정용 1회용 토큰."""

    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), index=True)
    token = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)


class AgentUser(Base):
    """콜센터 사용자(상담원·관리자) 계정."""

    __tablename__ = "agent_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    name = Column(String)
    email = Column(String, nullable=True)
    password_hash = Column(String)
    role = Column(String, default="agent")  # admin / agent
    active = Column(Boolean, default=True)
    failed_login_count = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


class AgentSession(Base):
    """콜센터 사용자 로그인 세션 (HttpOnly 쿠키 토큰)."""

    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("agent_users.id"), index=True)
    token = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


class Role(Base):
    """콜센터 사용자 역할. AgentUser.role 이 이 테이블의 name 을 참조한다."""

    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    is_system = Column(Boolean, default=False)  # True 면 삭제 불가
    created_at = Column(DateTime, default=_now)


class RolePermission(Base):
    """역할에 부여된 권한 키. admin 역할은 user_has() 가 항상 True 처리하므로 행을 두지 않는다."""

    __tablename__ = "role_permissions"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True)
    permission_key = Column(String, index=True)
    created_at = Column(DateTime, default=_now)


class FaqEntry(Base):
    """관리자가 큐레이션하는 FAQ 항목.

    초기 기동 시 코드의 시드 데이터로 1회 채워지고, 이후엔 DB가 권한적
    소스가 된다. 학습 항목은 별도 KnowledgeItem 테이블에 저장되어 GET
    /api/faq 응답에서 합산된다.
    """

    __tablename__ = "faq_entries"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, index=True)
    question = Column(Text)
    answer = Column(Text)
    keywords = Column(Text, nullable=True)  # JSON 배열 문자열
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
