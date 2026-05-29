"""비밀번호 재설정 지원 API.

대상 시스템 계정 테이블에서 사용자를 확인한 뒤, 1회용 토큰 기반으로
비밀번호 재설정 링크를 발급한다.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import accounts, auth, config
from ..database import get_db
from ..permissions import P
from ..schemas import PasswordQuery, PasswordResetConfirm

logger = logging.getLogger("ai_callcenter.password")

router = APIRouter(prefix="/api/password", tags=["password"])


@router.post("/verify")
def verify_account(
    payload: PasswordQuery,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.PASSWORD_ASSIST)),
):
    """대상 시스템 테이블에서 계정 정보를 확인한다 (권한: password.assist)."""
    account = accounts.find_account(db, payload.query)
    if account is None:
        return {"found": False, "account": None}
    return {"found": True, "account": accounts.account_public(account)}


@router.post("/reset-request")
def request_reset(
    payload: PasswordQuery,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(P.PASSWORD_ASSIST)),
):
    """계정 확인 후 재설정 토큰을 생성하고 링크를 발송한다 (권한: password.assist).

    SMTP가 설정돼 있으면 이메일로 발송하고, 없으면 서버 로그로 폴백한다
    (개발 편의를 위해 폴백 시에만 응답에 링크를 포함한다).
    """
    account = accounts.find_account(db, payload.query)
    if account is None:
        return {"sent": False, "message": "해당 정보와 일치하는 계정을 찾을 수 없습니다."}

    token = accounts.create_reset_token(db, account)
    base = config.APP_BASE_URL or str(request.base_url).rstrip("/")
    reset_link = f"{base}/reset?token={token}"
    email_masked = accounts.mask_email(account.email)

    if accounts.send_reset_email(account.email, reset_link):
        return {
            "sent": True,
            "channel": "email",
            "reset_link": None,
            "email_masked": email_masked,
            "expires_minutes": accounts.RESET_TOKEN_TTL_MINUTES,
            "message": f"{email_masked} 으로 재설정 링크를 발송했습니다.",
        }

    logger.info("[비밀번호 재설정 링크] %s -> %s", account.email, reset_link)
    return {
        "sent": True,
        "channel": "log",
        "reset_link": reset_link,
        "email_masked": email_masked,
        "expires_minutes": accounts.RESET_TOKEN_TTL_MINUTES,
        "message": "이메일(SMTP)이 설정되지 않아, 아래 재설정 링크를 고객에게 직접 안내해 주세요.",
    }


@router.get("/token/{token}")
def token_info(token: str, db: Session = Depends(get_db)):
    """재설정 토큰의 유효성을 확인한다 (재설정 페이지용)."""
    return accounts.token_status(db, token)


@router.post("/reset-confirm")
def reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    """재설정 토큰을 검증하고 새 비밀번호로 변경한다."""
    return accounts.confirm_reset(db, payload.token, payload.new_password)
