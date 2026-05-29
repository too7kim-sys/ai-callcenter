"""콜센터 사용자 관리 API (관리자 전용)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, permissions, security
from ..database import get_db
from ..models import AgentUser, Role
from ..schemas import AdminResetRequest, UserCreateRequest, UserUpdateRequest

router = APIRouter(prefix="/api/users", tags=["users"])


def _serialize(user: AgentUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "active": bool(user.active),
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("")
def list_users(_user=Depends(auth.require_permission(permissions.P.USER_MANAGE)), db: Session = Depends(get_db)):
    rows = db.query(AgentUser).order_by(AgentUser.id).all()
    return [_serialize(u) for u in rows]


@router.post("")
def create_user(
    payload: UserCreateRequest,
    _user=Depends(auth.require_permission(permissions.P.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    username = payload.username.lower().strip()
    if not username.isalnum() and not all(c.isalnum() or c in "._-" for c in username):
        raise HTTPException(status_code=400, detail="아이디는 영문/숫자/._- 만 사용 가능합니다.")
    if db.query(AgentUser).filter(AgentUser.username == username).first():
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
    role = (payload.role or permissions.ROLE_AGENT).strip().lower()
    if db.query(Role).filter(Role.name == role).first() is None:
        raise HTTPException(status_code=400, detail="존재하지 않는 역할입니다.")
    user = AgentUser(
        username=username,
        name=payload.name.strip(),
        email=(payload.email or "").strip() or None,
        password_hash=security.hash_password(payload.password),
        role=role,
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    acting_user=Depends(auth.require_permission(permissions.P.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    user = db.get(AgentUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.email is not None:
        user.email = payload.email.strip() or None
    if payload.role is not None:
        new_role = payload.role.strip().lower()
        if db.query(Role).filter(Role.name == new_role).first() is None:
            raise HTTPException(status_code=400, detail="존재하지 않는 역할입니다.")
        if user.id == acting_user.id and new_role != permissions.ROLE_ADMIN:
            raise HTTPException(status_code=400, detail="본인 관리자 권한은 해제할 수 없습니다.")
        user.role = new_role
    if payload.active is not None:
        if user.id == acting_user.id and not payload.active:
            raise HTTPException(status_code=400, detail="본인 계정은 비활성화할 수 없습니다.")
        user.active = bool(payload.active)
        if not user.active:
            auth.delete_user_sessions(db, user.id)  # 비활성화 즉시 로그아웃
    if payload.unlock:
        user.locked_until = None
        user.failed_login_count = 0
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.post("/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    payload: AdminResetRequest,
    _user=Depends(auth.require_permission(permissions.P.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    user = db.get(AgentUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    user.password_hash = security.hash_password(payload.new_password)
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    auth.delete_user_sessions(db, user.id)
    return {"ok": True, "message": "비밀번호를 초기화했습니다. 사용자에게 새 비밀번호를 전달해 주세요."}


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    acting_user=Depends(auth.require_permission(permissions.P.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="본인 계정은 삭제할 수 없습니다.")
    user = db.get(AgentUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    auth.delete_user_sessions(db, user.id)
    db.delete(user)
    db.commit()
    return {"ok": True}
