"""CSRF (Cross-Site Request Forgery) 방어 — Double-Submit Cookie 패턴.

설계:
  • 최초 요청 시 서버가 '_csrf_token' 쿠키를 발급 (HttpOnly X — JS 읽어야 함)
  • 클라이언트 JS 가 매 상태 변경 요청(POST/PUT/PATCH/DELETE)에 헤더로 동봉:
        X-CSRF-Token: <쿠키 값>
  • 미들웨어가 쿠키 == 헤더 일치 검증, 불일치/누락 → 403

  **검사 대상은 '인증 쿠키가 있는 요청' 만.**
  익명 엔드포인트(/api/conversations 익명 채팅 등) 는 CSRF 가 의미 없음
  (어차피 누구나 호출 가능). 검사 대상을 인증 세션 보유자에 한정해
  bypass 목록을 짧게 유지하고 운영 실수 표면을 줄임.

검증 우회 경로 (path-based — 인증 세션이 있어도 우회):
  • GET / HEAD / OPTIONS
  • /healthz, /readyz
  • /api/auth/login            — 로그인 자체는 비번이 1차 인증
  • /api/auth/2fa/verify       — 2FA 도 1차 인증의 일부
  • /api/events/stream         — SSE 응답 스트림

토큰 발급:
  • 토큰이 없는 클라이언트 응답에 Set-Cookie 로 자동 발급. JS 가 다음 요청부터 사용.
"""
from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("ai_callcenter.csrf")

CSRF_COOKIE = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SESSION_COOKIE = "ai_callcenter_session"  # auth.SESSION_COOKIE_NAME 과 동일

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# 인증 세션이 있어도 항상 우회 — 로그인 자체 / SSE / 헬스체크.
_PATH_BYPASS_PREFIXES = (
    "/healthz", "/readyz",
    "/api/auth/login", "/api/auth/2fa/verify",
    "/api/events/stream",
)


def _is_path_bypass(path: str) -> bool:
    return any(path.startswith(p) for p in _PATH_BYPASS_PREFIXES)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def rotate_csrf_cookie(response) -> None:
    """로그인/로그아웃 성공 시 CSRF 쿠키 회전 — 세션 고정 방어.

    공격자가 미리 심어둔 CSRF 값을 알고 있어도, 로그인 직후 무효화됨.
    """
    response.set_cookie(
        key=CSRF_COOKIE,
        value=_new_token(),
        httponly=False,
        samesite="lax",
        path="/",
        max_age=86400 * 30,
    )


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path
        is_safe = method in _SAFE_METHODS
        is_authed = bool(request.cookies.get(SESSION_COOKIE))

        # 검사 대상: 상태 변경 + 인증 세션 보유 + path bypass 아님.
        # 익명 요청은 CSRF 의미가 없으므로(어차피 누구나 호출 가능) 검사 X.
        if not is_safe and is_authed and not _is_path_bypass(path):
            cookie_tok = request.cookies.get(CSRF_COOKIE)
            header_tok = request.headers.get(CSRF_HEADER)
            if not cookie_tok or not header_tok or not secrets.compare_digest(cookie_tok, header_tok):
                logger.info("CSRF 차단 method=%s path=%s cookie=%s header=%s",
                            method, path, bool(cookie_tok), bool(header_tok))
                # 감사 로그 — 관리자가 /audit 에서 조회 가능
                from . import audit
                from .ratelimit import client_ip
                audit.log_middleware(
                    "security.csrf_block",
                    target_type="path", target_id=path[:200],
                    details={"method": method, "ip": client_ip(request),
                             "cookie_present": bool(cookie_tok),
                             "header_present": bool(header_tok)},
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 토큰이 누락되었거나 일치하지 않습니다."},
                )

        response = await call_next(request)

        # 토큰이 없으면 발급 (다음 상태 변경 요청에 사용)
        if not request.cookies.get(CSRF_COOKIE):
            # 새 토큰 — Secure 는 보안 헤더 미들웨어와 동일 정책,
            # HttpOnly=False (JS 가 읽어 헤더에 첨부해야 함)
            response.set_cookie(
                key=CSRF_COOKIE,
                value=_new_token(),
                httponly=False,
                samesite="lax",
                path="/",
                max_age=86400 * 30,  # 30일
            )
        return response
