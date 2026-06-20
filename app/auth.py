"""콜센터 사용자 인증·세션 관리.

세션 토큰은 HttpOnly + SameSite=Lax 쿠키로 클라이언트에 저장되어
JS에서 접근할 수 없고(XSS 방어), 다른 출처에서의 요청 시
자동 송출되지 않는다(CSRF 완화).
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from . import config, permissions, security
from .database import SessionLocal, get_db
from .models import AgentSession, AgentUser, Role, RolePermission

logger = logging.getLogger("ai_callcenter.auth")

SESSION_COOKIE_NAME = "ai_callcenter_session"
SESSION_TTL_HOURS = 8
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"
VALID_ROLES = {ROLE_ADMIN, ROLE_AGENT}


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    """SQLite는 datetime을 naive로 반환하므로 UTC 부여."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ====================================================================
# 인증
# ====================================================================

def authenticate(db: Session, username: str, password: str):
    """사용자명/비밀번호 검증. 성공 시 (user, None), 실패 시 (None, reason).

    reason: "invalid" | "inactive" | "locked"
    """
    name = (username or "").lower().strip()
    user = db.query(AgentUser).filter(AgentUser.username == name).first()
    if user is None:
        # 사용자명 노출 방지: 비밀번호 해시 계산 시간을 흉내내 타이밍 차이 최소화
        security.verify_password(password or "", "pbkdf2_sha256$200000$" + "A" * 24 + "$" + "B" * 44)
        return None, "invalid"
    if not user.active:
        return None, "inactive"
    if user.locked_until and _aware(user.locked_until) > _now():
        return None, "locked"
    if not security.verify_password(password or "", user.password_hash or ""):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = _now() + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
            logger.warning("계정 잠금: username=%s", user.username)
        db.commit()
        return None, "invalid"
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    db.commit()
    return user, None


# ====================================================================
# 세션
# ====================================================================

def create_session(db: Session, user: AgentUser, request: Request) -> str:
    token = security.generate_token()
    db.add(AgentSession(
        user_id=user.id,
        token=token,
        expires_at=_now() + timedelta(hours=SESSION_TTL_HOURS),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent", "") or "")[:300],
    ))
    db.commit()
    return token


def delete_session(db: Session, token: str | None):
    if not token:
        return
    record = db.query(AgentSession).filter(AgentSession.token == token).first()
    if record is not None:
        db.delete(record)
        db.commit()


def delete_user_sessions(db: Session, user_id: int):
    db.query(AgentSession).filter(AgentSession.user_id == user_id).delete(
        synchronize_session=False
    )
    db.commit()


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        # 운영(HTTPS) 환경에서는 .env 의 COOKIE_SECURE=true 로 설정.
        # 'auto' 면 APP_BASE_URL 이 https:// 로 시작할 때 자동 True.
        secure=config.COOKIE_SECURE,
        max_age=SESSION_TTL_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


# ====================================================================
# FastAPI 의존성
# ====================================================================

def current_user(request: Request, db: Session = Depends(get_db)) -> AgentUser:
    """쿠키의 세션 토큰을 검증하고 현재 사용자를 반환한다."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    sess = db.query(AgentSession).filter(AgentSession.token == token).first()
    if sess is None:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다.")
    expires = _aware(sess.expires_at)
    if expires is None or expires < _now():
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다.")
    user = db.get(AgentUser, sess.user_id)
    if user is None or not user.active:
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail="유효하지 않은 사용자입니다.")
    return user


def require_agent(user: AgentUser = Depends(current_user)) -> AgentUser:
    """상담원 이상 권한 (로그인된 모든 사용자)."""
    return user


def require_admin(user: AgentUser = Depends(current_user)) -> AgentUser:
    """관리자 전용 (역할이 admin인 경우)."""
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def require_permission(perm_key: str):
    """특정 권한이 필요한 엔드포인트용 FastAPI 의존성."""

    def checker(
        user: AgentUser = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> AgentUser:
        if not permissions.user_has(db, user, perm_key):
            raise HTTPException(status_code=403, detail="이 작업을 수행할 권한이 없습니다.")
        return user

    return checker


# ====================================================================
# 시드
# ====================================================================

def seed_admin():
    """초기 admin 계정. 비밀번호는 ADMIN_INIT_PASSWORD 환경변수로 override 가능."""
    db = SessionLocal()
    try:
        if db.query(AgentUser).count() > 0:
            return
        password = os.getenv("ADMIN_INIT_PASSWORD", "admin1234").strip() or "admin1234"
        user = AgentUser(
            username="admin",
            name="관리자",
            email="admin@ai-callcenter.local",
            password_hash=security.hash_password(password),
            role=ROLE_ADMIN,
            active=True,
        )
        db.add(user)
        db.commit()
        logger.info(
            "초기 admin 계정을 생성했습니다 (username=admin, password=%s). "
            "로그인 직후 비밀번호를 변경해 주세요.",
            password,
        )
    finally:
        db.close()


def seed_roles():
    """시스템 역할(admin, agent)을 1회 시드한다."""
    db = SessionLocal()
    try:
        if db.query(Role).filter(Role.name == permissions.ROLE_ADMIN).first() is None:
            db.add(Role(
                name=permissions.ROLE_ADMIN,
                description="모든 권한을 가진 관리자",
                is_system=True,
            ))
        agent_role = db.query(Role).filter(Role.name == permissions.ROLE_AGENT).first()
        if agent_role is None:
            agent_role = Role(
                name=permissions.ROLE_AGENT,
                description="일반 상담원 (기본 권한)",
                is_system=True,
            )
            db.add(agent_role)
            db.commit()
            db.refresh(agent_role)
            for key in permissions.DEFAULT_AGENT_PERMISSIONS:
                db.add(RolePermission(role_id=agent_role.id, permission_key=key))
        db.commit()
    finally:
        db.close()
