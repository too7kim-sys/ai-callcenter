"""민감정보(PII) 마스킹.

저장·전송 시점에 텍스트에서 한국 환경에서 흔한 PII 패턴을 자동 마스킹한다.
주 적용 지점:
  • 지식 베이스(RAG) 자동 학습 — KnowledgeItem.question/answer 영구 저장 전
  • CSV 내보내기 — 대량 유출 방어
  • Audit log details — 운영자가 볼 가능성

설계 원칙:
  • 보수적: 잘못된 매칭으로 의미가 망가지지 않도록 패턴을 엄격하게.
  • 일부는 남기기: 식별 가능하되 확인 가능하게 (전화 010-****-5678).
  • 한국식 패턴 우선. 일반적인 카드/이메일/IP/IBAN 도 포함.
"""
from __future__ import annotations

import re

# 휴대전화 010-1234-5678 / 010 1234 5678 / 01012345678
_PHONE = re.compile(r"\b(01[016789])[-\s.]?(\d{3,4})[-\s.]?(\d{4})\b")

# 일반 전화 02-1234-5678 / 031-123-4567
_LANDLINE = re.compile(r"\b(0[2-6][0-9]?)[-\s.](\d{3,4})[-\s.](\d{4})\b")

# 이메일
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,3})([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# 주민등록번호 (보수적 — YYMMDD-숫자7)
_RRN = re.compile(r"\b(\d{6})-([1-4])(\d{6})\b")

# 카드번호 16자리 (4그룹) — 1234-5678-1234-5678 또는 16자리 연속
_CARD = re.compile(r"\b(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})\b")

# 계좌번호 — 한국 은행은 형식이 다양하므로 보수적: 숫자 10~14자리, 하이픈 포함
_ACCOUNT = re.compile(r"\b(\d{2,4})-(\d{2,6})-(\d{4,8})\b")

# IP 주소 (선택적 — 운영 디버그용)
_IP = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


def mask_text(text: str | None) -> str | None:
    """문자열 내 PII 를 마스킹해 반환. None/빈 문자열은 그대로."""
    if not text:
        return text

    s = text
    s = _RRN.sub(lambda m: f"{m.group(1)}-{m.group(2)}******", s)
    # 카드 먼저 (계좌 패턴이 카드 일부를 잡지 않도록)
    s = _CARD.sub(lambda m: f"{m.group(1)}-****-****-{m.group(4)}", s)
    s = _PHONE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    s = _LANDLINE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    s = _ACCOUNT.sub(lambda m: f"{m.group(1)}-****-{m.group(3)[-4:]}", s)
    s = _EMAIL.sub(_mask_email, s)
    return s


def _mask_email(m: re.Match) -> str:
    head, rest, domain = m.group(1), m.group(2), m.group(3)
    return f"{head[:2]}***@{domain}"


def has_pii(text: str | None) -> bool:
    """PII 포함 여부 — 마스킹 전후 비교로 판정."""
    if not text:
        return False
    return mask_text(text) != text
