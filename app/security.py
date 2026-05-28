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
