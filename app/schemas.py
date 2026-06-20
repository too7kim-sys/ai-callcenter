"""요청 본문 스키마."""
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    customer_name: str = Field(default="고객", max_length=60)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class StatusRequest(BaseModel):
    status: str  # open / escalated / closed


class KnowledgeItemUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)


class FaqCreate(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=4000)
    keywords: list[str] = Field(default_factory=list)


class FaqUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=64)
    question: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)
    keywords: list[str] | None = None


class AssignRequest(BaseModel):
    user_id: int | None = None  # None = 배정 해제


class TemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="", max_length=64)


class TemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=64)


class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class FeedbackRequest(BaseModel):
    value: str  # "up" / "down" / "" (해제)


class CustomerEndRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str | None = Field(default=None, max_length=1000)


class AgentRequestBody(BaseModel):
    note: str = Field(default="", max_length=500)


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = Field(default=None, max_length=64)


class CallbackCreate(BaseModel):
    customer_name: str | None = Field(default=None, max_length=100)
    phone: str = Field(min_length=4, max_length=40)
    preferred_text: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    conversation_id: int | None = None


class CallbackUpdate(BaseModel):
    status: str  # contacted / completed / cancelled
    note: str | None = Field(default=None, max_length=1000)


class CallRequest(BaseModel):
    conversation_id: int
    customer_name: str | None = Field(default=None, max_length=100)


class CallSignalRequest(BaseModel):
    """WebRTC SDP / ICE candidate 전달용.

    인증: 익명 고객은 customer_token (통화 요청 시 발급된 1회용 토큰),
          상담원은 세션 쿠키. 둘 중 하나라도 없으면 401.
    """
    kind: str = Field(min_length=1, max_length=20)   # offer / answer / ice / hangup
    payload: dict = Field(default_factory=dict)
    customer_token: str | None = Field(default=None, max_length=128)


class CallEndRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=40)


class PasswordQuery(BaseModel):
    query: str = Field(min_length=1, max_length=200)  # 이메일 또는 아이디


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=8, max_length=100)


class TwoFactorVerifyRequest(BaseModel):
    pending_token: str = Field(min_length=8, max_length=128)
    code: str = Field(min_length=4, max_length=20)


class TwoFactorSetupRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class TwoFactorDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    email: str = Field(default="", max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: str = "agent"


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=200)
    role: str | None = None
    active: bool | None = None
    unlock: bool = False


class AdminResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)
