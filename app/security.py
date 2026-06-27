"""비밀번호 해시 / 토큰 생성 공통 유틸.

PBKDF2-SHA256(200k iter) + 상수시간 비교 + secrets 기반 토큰.
accounts(대상 시스템 사용자)과 auth(콜센터 사용자) 양쪽에서 공유한다.
"""
import base64
import hashlib
import hmac
import os
import secrets

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    """저장된 해시와 입력 비밀번호를 상수시간으로 비교한다."""
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def generate_token() -> str:
    """세션·재설정 등에 사용할 URL-safe 랜덤 토큰."""
    return secrets.token_urlsafe(32)


# ====================================================================
# 비밀번호 정책 — KISA/행안부 시큐어코딩 가이드 권고에 맞춰
#   • 최소 10자
#   • 영문 / 숫자 / 특수문자 중 3종 이상 조합
#   • username/email 과 동일하거나 포함하면 거부
# ====================================================================

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256
PASSWORD_POLICY_DESC = (
    f"{PASSWORD_MIN_LENGTH}자 이상, 영문/숫자/특수문자 중 3종 이상 조합"
)


def validate_password_strength(password: str, *, username: str | None = None,
                                email: str | None = None) -> str | None:
    """반환: 통과 시 None, 실패 시 사용자에게 보일 메시지(첫 위반 사유)."""
    if not isinstance(password, str):
        return "비밀번호 형식이 올바르지 않습니다."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"비밀번호는 {PASSWORD_MAX_LENGTH}자 이하여야 합니다."
    classes = 0
    if any(c.islower() for c in password): classes += 1
    if any(c.isupper() for c in password): classes += 1
    if any(c.isdigit() for c in password): classes += 1
    if any(not c.isalnum() for c in password): classes += 1
    if classes < 3:
        return "비밀번호는 영문 소문자/대문자/숫자/특수문자 중 3종 이상을 포함해야 합니다."
    pw_lower = password.lower()
    for forbidden in ("password", "admin", "qwerty", "1234"):
        if forbidden in pw_lower:
            return "비밀번호에 흔히 추측되는 단어(password/admin/qwerty/1234)는 사용할 수 없습니다."
    if username and len(username) >= 4 and username.lower() in pw_lower:
        return "비밀번호에 사용자명을 포함할 수 없습니다."
    if email:
        local = email.split("@")[0].lower()
        if local and len(local) >= 4 and local in pw_lower:
            return "비밀번호에 이메일 로컬 파트를 포함할 수 없습니다."
    return None
