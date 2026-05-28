"""인증 API: 로그인 / 로그아웃 / 본인 정보 / 본인 비밀번호 변경."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import auth, security
from ..database import get_db
from ..schemas import LoginRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
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
        # invalid 는 username 노출 방지를 위해 항상 동일 메시지
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    token = auth.create_session(db, user, request)
    auth.set_session_cookie(response, token)
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": user.role,
    }


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.delete_session(db, request.cookies.get(auth.SESSION_COOKIE_NAME))
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(auth.current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": user.role,
    }


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
