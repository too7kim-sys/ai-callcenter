"""인증 API: 로그인 / 로그아웃 / 본인 정보 / 본인 비밀번호 변경 / 2FA."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import audit, auth, permissions, ratelimit, security, twofa
from ..database import get_db
from ..models import AgentUser
from ..schemas import (
    LoginRequest,
    PasswordChangeRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupRequest,
    TwoFactorVerifyRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(user: AgentUser):
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        "totp_enabled": bool(user.totp_enabled),
    }


@router.post(
    "/login",
    dependencies=[Depends(ratelimit.rate_limit("login", 10, 60))],
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user, reason = auth.authenticate(db, payload.username, payload.password)
    if user is None:
        if reason == "locked":
            raise HTTPException(
                status_code=423,
                detail="로그인 시도가 많아 계정이 일시적으로 잠겼습니다. 잠시 후 다시 시도해 주세요.",
            )
        if reason == "inactive":
            raise HTTPException(
                status_code=403,
                detail="비활성 계정입니다. 관리자에게 문의해 주세요.",
            )
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    # 2FA 가 켜져 있으면 임시 토큰 발급, 세션은 코드 검증 후 발급
    if user.totp_enabled and user.totp_secret:
        pending = twofa.issue_pending_token(user.id)
        return {"needs_2fa": True, "pending_token": pending, "username": user.username}

    token = auth.create_session(db, user, request)
    auth.set_session_cookie(response, token)
    return _user_payload(user)


@router.post(
    "/2fa/verify",
    dependencies=[Depends(ratelimit.rate_limit("2fa_verify", 10, 60))],
)
def twofa_verify(
    payload: TwoFactorVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """로그인 2단계 — 임시 토큰 + 6자리 TOTP 코드 (또는 복구 코드 XXXX-XXXX)."""
    user_id = twofa.consume_pending_token(payload.pending_token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="2단계 인증 세션이 만료되었습니다. 다시 로그인해 주세요.")
    user = db.get(AgentUser, user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=403, detail="계정을 사용할 수 없습니다.")

    code = (payload.code or "").strip()
    ok = False
    used_recovery = False
    if len(code) == 9 and "-" in code:
        # 복구 코드 사용 — 1회용
        ok, updated = twofa.consume_recovery_code(user.totp_recovery, code.upper())
        if ok:
            user.totp_recovery = updated
            used_recovery = True
            db.commit()
    else:
        ok = twofa.verify_code(user.totp_secret, code)

    if not ok:
        # 새 임시 토큰 재발급 — 무차별 대입 차단을 위해 동일 토큰 재사용 X
        new_token = twofa.issue_pending_token(user.id)
        raise HTTPException(
            status_code=401,
            detail="인증 코드가 올바르지 않습니다.",
            headers={"X-Pending-Token": new_token},
        )

    token = auth.create_session(db, user, request)
    auth.set_session_cookie(response, token)
    if used_recovery:
        audit.log(db, user, "auth.2fa.recovery_used",
                  target_type="user", target_id=user.id)
    return {**_user_payload(user), "used_recovery": used_recovery}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.delete_session(db, request.cookies.get(auth.SESSION_COOKIE_NAME))
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return {
        **_user_payload(user),
        "permissions": permissions.permissions_of(db, user.role),
    }


# ====================================================================
# 2FA 설정 — 사용자가 본인 계정에 TOTP 활성/비활성
# ====================================================================

@router.post("/2fa/setup")
def twofa_setup(user=Depends(auth.current_user), db: Session = Depends(get_db)):
    """1단계 — 비밀(secret) 생성 + 프로비저닝 URI + QR SVG 반환.

    호출자는 이 비밀을 인증기 앱에 등록하고, /2fa/activate 에 6자리 코드를
    보내야 실제 활성화된다. 활성화 전까지는 totp_enabled=False 유지.
    """
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="이미 2FA 가 활성화되어 있습니다.")
    secret = twofa.make_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    db.commit()
    uri = twofa.make_provisioning_uri(secret, user.username)
    return {
        "secret": secret,
        "uri": uri,
        "qr_svg": twofa.make_qr_svg(uri),
    }


@router.post("/2fa/activate")
def twofa_activate(
    payload: TwoFactorSetupRequest,
    user=Depends(auth.current_user),
    db: Session = Depends(get_db),
):
    """2단계 — 인증기에서 받은 6자리 코드로 확인 후 활성화. 복구 코드 발급."""
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="이미 2FA 가 활성화되어 있습니다.")
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="먼저 /2fa/setup 으로 비밀을 발급받으세요.")
    if not twofa.verify_code(user.totp_secret, payload.code):
        raise HTTPException(status_code=401, detail="인증 코드가 올바르지 않습니다.")
    recovery = twofa.generate_recovery_codes()
    user.totp_enabled = True
    user.totp_recovery = twofa.hash_recovery_codes(recovery)
    db.commit()
    audit.log(db, user, "auth.2fa.activated", target_type="user", target_id=user.id)
    return {
        "ok": True,
        "recovery_codes": recovery,
        "message": "2FA 가 활성화되었습니다. 복구 코드를 안전한 곳에 보관하세요. 이 화면을 닫으면 다시 표시되지 않습니다.",
    }


@router.post("/2fa/disable")
def twofa_disable(
    payload: TwoFactorDisableRequest,
    user=Depends(auth.current_user),
    db: Session = Depends(get_db),
):
    """본인이 비밀번호 재확인 후 비활성화. (관리자 강제 해제는 별도 흐름)."""
    if not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_recovery = None
    db.commit()
    audit.log(db, user, "auth.2fa.disabled", target_type="user", target_id=user.id)
    return {"ok": True}


@router.post("/change-password")
def change_password(
    payload: PasswordChangeRequest,
    user=Depends(auth.current_user),
    db: Session = Depends(get_db),
):
    """본인 비밀번호 변경. 변경 후 본인의 모든 세션은 무효화된다."""
    if not security.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="기존 비밀번호와 다른 비밀번호를 사용해 주세요.")
    user.password_hash = security.hash_password(payload.new_password)
    db.commit()
    auth.delete_user_sessions(db, user.id)
    return {"ok": True, "message": "비밀번호가 변경되었습니다. 다시 로그인해 주세요."}
