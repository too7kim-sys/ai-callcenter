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


class PasswordQuery(BaseModel):
    query: str = Field(min_length=1, max_length=200)  # 이메일 또는 아이디


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=8, max_length=100)
