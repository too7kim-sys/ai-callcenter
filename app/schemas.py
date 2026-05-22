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
