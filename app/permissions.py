"""권한 카탈로그 + 역할 권한 검사 유틸.

각 엔드포인트는 사용자 역할이 특정 권한 키를 갖는지 확인하여 접근을 제어한다.
`admin` 역할은 항상 모든 권한을 갖는다 (잠금 방지 및 운영 편의).
"""
from typing import List, Tuple

from sqlalchemy.orm import Session


class P:
    """권한 키 상수 (자동완성·오타 방지용)."""
    CONV_VIEW = "conversation.view"
    CONV_REPLY = "conversation.reply"
    CONV_ANALYZE = "conversation.analyze"
    CONV_RECOMMEND = "conversation.recommend"
    CONV_CLOSE = "conversation.close"
    KNOWLEDGE_VIEW = "knowledge.view"
    KNOWLEDGE_DELETE = "knowledge.delete"
    FAQ_VIEW = "faq.view"
    FAQ_MANAGE = "faq.manage"
    VOICE_UPLOAD = "voice.upload"
    PASSWORD_ASSIST = "password.assist"
    USER_MANAGE = "user.manage"
    PERMISSION_MANAGE = "permission.manage"
    AUDIT_VIEW = "audit.view"
    TEMPLATE_MANAGE = "template.manage"
    DATA_EXPORT = "data.export"


# 카탈로그: (key, 설명, 그룹). 그룹은 UI 정렬·묶기에 사용.
CATALOG: List[Tuple[str, str, str]] = [
    (P.CONV_VIEW,        "상담 목록·상세 조회",        "상담"),
    (P.CONV_REPLY,       "상담원 답변 전송",           "상담"),
    (P.CONV_ANALYZE,     "상담 요약·분류 실행",        "상담"),
    (P.CONV_RECOMMEND,   "답변 추천 받기",             "상담"),
    (P.CONV_CLOSE,       "상담 종료 (학습 트리거)",    "상담"),
    (P.KNOWLEDGE_VIEW,   "학습 데이터 조회",           "학습"),
    (P.KNOWLEDGE_DELETE, "학습 데이터 삭제",           "학습"),
    (P.FAQ_VIEW,         "FAQ 조회",                   "FAQ"),
    (P.FAQ_MANAGE,       "FAQ 추가·수정·삭제",         "FAQ"),
    (P.VOICE_UPLOAD,     "음성 파일 학습 (STT)",       "FAQ"),
    (P.PASSWORD_ASSIST,  "고객 비밀번호 재설정 지원",  "고객 지원"),
    (P.USER_MANAGE,      "콜센터 사용자 관리",         "관리"),
    (P.PERMISSION_MANAGE, "권한·역할 관리",            "관리"),
    (P.AUDIT_VIEW,       "변경 이력(audit log) 조회", "관리"),
    (P.TEMPLATE_MANAGE,  "답변 템플릿 관리",           "관리"),
    (P.DATA_EXPORT,      "데이터 내보내기 (CSV)",      "관리"),
]

ALL_PERMISSION_KEYS = {key for key, _, _ in CATALOG}


def catalog_as_list():
    return [{"key": k, "description": d, "group": g} for k, d, g in CATALOG]


# ====================================================================
# 시스템 역할 (시드용)
# ====================================================================

ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"

# admin은 user_has() 에서 항상 True 처리하므로 권한 저장 불필요.
# agent의 기본 권한: 일상 응대 + 조회. 삭제·관리 권한 제외.
DEFAULT_AGENT_PERMISSIONS = {
    P.CONV_VIEW, P.CONV_REPLY, P.CONV_ANALYZE, P.CONV_RECOMMEND, P.CONV_CLOSE,
    P.KNOWLEDGE_VIEW, P.FAQ_VIEW, P.PASSWORD_ASSIST,
}


# ====================================================================
# 권한 검사
# ====================================================================

def user_has(db: Session, user, perm_key: str) -> bool:
    """사용자가 특정 권한을 가지고 있는지 확인."""
    from .models import Role, RolePermission
    if user is None or not getattr(user, "active", False):
        return False
    if user.role == ROLE_ADMIN:
        return True  # admin은 항상 모든 권한
    role = db.query(Role).filter(Role.name == user.role).first()
    if role is None:
        return False  # 역할이 존재하지 않으면 거부
    record = (
        db.query(RolePermission)
        .filter(
            RolePermission.role_id == role.id,
            RolePermission.permission_key == perm_key,
        )
        .first()
    )
    return record is not None


def permissions_of(db: Session, role_name: str) -> List[str]:
    """역할이 가진 권한 키 목록을 반환. admin 은 전체."""
    from .models import Role, RolePermission
    if role_name == ROLE_ADMIN:
        return sorted(ALL_PERMISSION_KEYS)
    role = db.query(Role).filter(Role.name == role_name).first()
    if role is None:
        return []
    rows = db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
    return sorted(r.permission_key for r in rows)
