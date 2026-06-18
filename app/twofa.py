"""2단계 인증 (TOTP, RFC 6238) — Google Authenticator / Authy 호환.

설계:
  • pyotp 로 TOTP 검증, qrcode 로 SVG 프로비저닝 코드 인라인 생성.
  • 비밀(secret)·복구 코드는 DB 의 AgentUser 행에 저장.
  • 복구 코드는 평문 저장하지 않고 SHA-256 해시 비교 (1회용).
  • 로그인 2단계:
      1) /api/auth/login         — 비번 검증 OK → 2FA 미사용이면 세션 발급 종료
                                                 2FA 사용이면 임시 토큰만 발급
      2) /api/auth/2fa/verify    — 임시 토큰 + 6자리 코드 → 세션 발급
  • 임시 토큰은 인메모리, 5분 TTL.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import secrets
import threading
import time
from typing import Optional

import pyotp
import qrcode
from qrcode.image.svg import SvgPathImage

logger = logging.getLogger("ai_callcenter.twofa")

ISSUER = "AI 콜센터"

_PENDING: dict[str, tuple[int, float]] = {}  # token -> (user_id, expires_ts)
_PENDING_LOCK = threading.Lock()
_PENDING_TTL_SECS = 300


def make_secret() -> str:
    return pyotp.random_base32()


def make_provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def make_qr_svg(uri: str) -> str:
    """프로비저닝 URI → SVG 문자열 (외부 의존 X, 인라인 표시 가능)."""
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(image_factory=SvgPathImage).save(buf)
    return buf.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    """TOTP 코드 검증. 이전/다음 30초 윈도우(±1) 허용."""
    if not secret or not code:
        return False
    code = code.replace(" ", "").strip()
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_recovery_codes(count: int = 10) -> list[str]:
    """사람이 받아 적기 좋은 형식 (XXXX-XXXX)."""
    out = []
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0/O/1/I 제외
    for _ in range(count):
        a = "".join(secrets.choice(alpha) for _ in range(4))
        b = "".join(secrets.choice(alpha) for _ in range(4))
        out.append(f"{a}-{b}")
    return out


def hash_recovery_codes(codes: list[str]) -> str:
    """복구 코드를 sha256 해시로 변환해 JSON 배열 문자열로 직렬화."""
    return json.dumps([_hash(c) for c in codes])


def consume_recovery_code(stored_json: str | None, candidate: str) -> tuple[bool, str | None]:
    """후보 코드와 매치되는 해시 항목을 찾아 삭제. (성공, 갱신할 JSON) 반환."""
    if not stored_json:
        return False, None
    try:
        hashes = json.loads(stored_json)
    except Exception:
        return False, None
    cand_hash = _hash(candidate.replace(" ", "").upper())
    for i, h in enumerate(hashes):
        if hmac.compare_digest(h, cand_hash):
            del hashes[i]
            return True, json.dumps(hashes)
    return False, None


# --------------------------------------------------------------------
# 2FA 임시 토큰 (1단계 통과 후 2단계 대기 상태)
# --------------------------------------------------------------------

def issue_pending_token(user_id: int) -> str:
    _cleanup_pending()
    token = secrets.token_urlsafe(24)
    expires = time.monotonic() + _PENDING_TTL_SECS
    with _PENDING_LOCK:
        _PENDING[token] = (user_id, expires)
    return token


def consume_pending_token(token: str) -> int | None:
    """토큰 검증 + 1회 소비. 유효하면 user_id, 아니면 None."""
    if not token:
        return None
    with _PENDING_LOCK:
        entry = _PENDING.pop(token, None)
    if entry is None:
        return None
    user_id, expires = entry
    if time.monotonic() > expires:
        return None
    return user_id


def _cleanup_pending():
    now = time.monotonic()
    with _PENDING_LOCK:
        for tok in [t for t, (_, exp) in _PENDING.items() if exp < now]:
            _PENDING.pop(tok, None)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
