"""역할·권한 관리 API (관리자 전용).

권한 카탈로그는 코드 상수, 역할은 DB 테이블, 역할↔권한은 매핑 테이블.
admin 역할은 항상 모든 권한을 갖는 것으로 처리되므로 권한 편집 불가.
시스템 역할(admin, agent)은 삭제 불가.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth, permissions
from ..database import get_db
from ..models import AgentUser, Role, RolePermission

router = APIRouter(prefix="/api/roles", tags=["roles"])


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    description: str = Field(default="", max_length=200)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    permissions: list[str] | None = None


def _serialize(role: Role, perm_keys: list[str], user_count: int) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description or "",
        "is_system": bool(role.is_system),
        "permissions": perm_keys,
        "user_count": user_count,
        "all_permissions": role.name == permissions.ROLE_ADMIN,
    }


def _user_count(db: Session, role_name: str) -> int:
    return db.query(AgentUser).filter(AgentUser.role == role_name).count()


@router.get("/catalog")
def list_catalog(
    _user=Depends(auth.require_permission(permissions.P.PERMISSION_MANAGE)),
):
    """전체 권한 카탈로그 (UI 체크박스 렌더링용)."""
    return permissions.catalog_as_list()


@router.get("")
def list_roles(
    db: Session = Depends(get_db),
    user=Depends(auth.current_user),
):
    """역할 목록 — 권한·역할 관리(편집용) 또는 사용자 관리(역할 부여 드롭다운용) 권한 필요."""
    if not (
        permissions.user_has(db, user, permissions.P.PERMISSION_MANAGE)
        or permissions.user_has(db, user, permissions.P.USER_MANAGE)
    ):
        raise HTTPException(status_code=403, detail="이 작업을 수행할 권한이 없습니다.")
    rows = db.query(Role).order_by(Role.id).all()
    return [
        _serialize(r, permissions.permissions_of(db, r.name), _user_count(db, r.name))
        for r in rows
    ]


@router.post("")
def create_role(
    payload: RoleCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(permissions.P.PERMISSION_MANAGE)),
):
    name = payload.name.strip().lower()
    if not all(c.isalnum() or c in "._-" for c in name):
        raise HTTPException(status_code=400, detail="역할 이름은 영문/숫자/._- 만 사용 가능합니다.")
    if name in {permissions.ROLE_ADMIN, permissions.ROLE_AGENT}:
        raise HTTPException(status_code=400, detail="시스템 역할 이름은 사용할 수 없습니다.")
    if db.query(Role).filter(Role.name == name).first():
        raise HTTPException(status_code=409, detail="이미 사용 중인 역할 이름입니다.")
    role = Role(
        name=name,
        description=(payload.description or "").strip() or None,
        is_system=False,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    _set_permissions(db, role, payload.permissions)
    return _serialize(role, permissions.permissions_of(db, role.name), 0)


@router.patch("/{role_id}")
def update_role(
    role_id: int,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(permissions.P.PERMISSION_MANAGE)),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="역할을 찾을 수 없습니다.")
    if payload.description is not None:
        role.description = payload.description.strip() or None
    if payload.permissions is not None:
        if role.name == permissions.ROLE_ADMIN:
            raise HTTPException(status_code=400, detail="admin 역할의 권한은 변경할 수 없습니다.")
        _set_permissions(db, role, payload.permissions)
    db.commit()
    return _serialize(role, permissions.permissions_of(db, role.name), _user_count(db, role.name))


@router.delete("/{role_id}")
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _user=Depends(auth.require_permission(permissions.P.PERMISSION_MANAGE)),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="역할을 찾을 수 없습니다.")
    if role.is_system:
        raise HTTPException(status_code=400, detail="시스템 역할은 삭제할 수 없습니다.")
    in_use = _user_count(db, role.name)
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"이 역할을 사용 중인 사용자가 {in_use}명 있어 삭제할 수 없습니다.",
        )
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete(
        synchronize_session=False
    )
    db.delete(role)
    db.commit()
    return {"ok": True}


def _set_permissions(db: Session, role: Role, keys):
    """역할의 권한을 주어진 키 집합으로 교체한다."""
    keys = [k for k in (keys or []) if k in permissions.ALL_PERMISSION_KEYS]
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete(
        synchronize_session=False
    )
    for k in keys:
        db.add(RolePermission(role_id=role.id, permission_key=k))
    db.commit()
