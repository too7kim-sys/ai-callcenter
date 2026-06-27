"""대상 시스템 사용자 계정 + 비밀번호 재설정 지원.

콜센터가 지원하는 서비스의 계정을 조회하고, 1회용 토큰 기반으로
비밀번호 재설정을 처리한다. 재설정 링크는 SMTP가 설정돼 있으면
이메일로 발송하고, 없으면 서버 로그로 폴백한다.
"""
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import config
from .database import SessionLocal
from .models import Account, PasswordResetToken
from .security import (  # noqa: F401
    generate_token, hash_password, verify_password, validate_password_strength,
)

logger = logging.getLogger("ai_callcenter.accounts")

RESET_TOKEN_TTL_MINUTES = 30

_SEED_ACCOUNTS = [
    {"username": "minseo", "email": "minseo@example.com", "name": "김민서", "phone": "010-1234-5678"},
    {"username": "junho", "email": "junho@example.com", "name": "이준호", "phone": "010-2345-6789"},
    {"username": "jiyoung", "email": "jiyoung@example.com", "name": "박지영", "phone": "010-3456-7890"},
]
_SEED_PASSWORD = "password1234"


def _now():
    return datetime.now(timezone.utc)


# ====================================================================
# 마스킹 (민감정보 노출 최소화)
# ====================================================================

def mask_email(email):
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'*' * max(3, len(local) - len(shown))}@{domain}"


def mask_phone(phone):
    if not phone:
        return None
    parts = phone.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-****-{parts[2]}"
    return phone[:3] + "****"


# ====================================================================
# 계정 조회
# ====================================================================

def find_account(db, query):
    """이메일 또는 아이디로 대상 시스템 계정을 조회한다."""
    normalized = (query or "").strip().lower()
    if not normalized:
        return None
    return (
        db.query(Account)
        .filter((Account.email == normalized) | (Account.username == normalized))
        .first()
    )


def account_public(account):
    """외부 노출용 계정 정보 (민감정보 마스킹)."""
    return {
        "name": account.name,
        "username": account.username,
        "email_masked": mask_email(account.email),
        "phone_masked": mask_phone(account.phone),
    }


# ====================================================================
# 재설정 토큰
# ====================================================================

def create_reset_token(db, account):
    """계정에 대한 1회용 재설정 토큰을 생성·저장하고 토큰 문자열을 반환한다."""
    token = generate_token()
    db.add(PasswordResetToken(
        account_id=account.id,
        token=token,
        expires_at=_now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
        used=False,
    ))
    db.commit()
    return token


def get_valid_token(db, token):
    """유효한(미사용·미만료) 토큰 레코드를 반환한다. 없으면 None."""
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if record is None or record.used:
        return None
    expires = record.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # SQLite는 naive로 반환
    if expires is None or expires < _now():
        return None
    return record


def token_status(db, token):
    """재설정 토큰의 유효성과 대상 계정 정보를 반환한다 (재설정 페이지용)."""
    record = get_valid_token(db, token)
    if record is None:
        return {"valid": False, "email_masked": None,
                "message": "유효하지 않거나 만료된 재설정 링크입니다."}
    account = db.get(Account, record.account_id)
    return {
        "valid": True,
        "email_masked": mask_email(account.email) if account else None,
        "message": "유효한 링크입니다. 새 비밀번호를 설정해 주세요.",
    }


def confirm_reset(db, token, new_password):
    """토큰을 검증하고 새 비밀번호로 변경한다."""
    record = get_valid_token(db, token)
    if record is None:
        return {"ok": False, "message": "유효하지 않거나 만료된 링크입니다. 재설정을 다시 요청해 주세요."}
    account = db.get(Account, record.account_id)
    if account is None:
        return {"ok": False, "message": "계정을 찾을 수 없습니다."}
    policy_err = validate_password_strength(
        new_password, username=account.username, email=account.email,
    )
    if policy_err:
        return {"ok": False, "message": policy_err}
    account.password_hash = hash_password(new_password)
    record.used = True
    db.commit()
    return {"ok": True, "message": "비밀번호가 성공적으로 재설정되었습니다."}


# ====================================================================
# 이메일 발송 (SMTP 미설정 시 False 반환 → 호출부에서 로그 폴백)
# ====================================================================

def send_reset_email(to_email, reset_link):
    if not config.SMTP_HOST:
        return False
    message = EmailMessage()
    message["From"] = config.SMTP_FROM
    message["To"] = to_email
    message["Subject"] = "[AI 콜센터] 비밀번호 재설정 안내"
    message.set_content(
        "안녕하세요.\n\n"
        f"아래 링크에서 비밀번호를 재설정해 주세요 (유효시간 {RESET_TOKEN_TTL_MINUTES}분):\n\n"
        f"{reset_link}\n\n"
        "본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다."
    )
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
            if config.SMTP_USER:
                server.starttls()
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception as exc:
        logger.warning("재설정 메일 발송 실패, 로그 폴백으로 전환: %s", exc)
        return False


# ====================================================================
# 데모 계정 시드
# ====================================================================

def seed_accounts():
    """데모용 대상 시스템 계정을 1회 생성한다."""
    db = SessionLocal()
    try:
        if db.query(Account).count() > 0:
            return
        for item in _SEED_ACCOUNTS:
            db.add(Account(
                username=item["username"],
                email=item["email"],
                name=item["name"],
                phone=item["phone"],
                password_hash=hash_password(_SEED_PASSWORD),
            ))
        db.commit()
        logger.info("데모 계정 %d개를 생성했습니다 (기본 비밀번호: %s).",
                    len(_SEED_ACCOUNTS), _SEED_PASSWORD)
    finally:
        db.close()
