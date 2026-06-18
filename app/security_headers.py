"""보안 헤더 미들웨어.

기본 헤더 (모든 경로):
  • Content-Security-Policy   — XSS·인라인 스크립트 방어
  • Strict-Transport-Security — HTTPS 강제 (HTTPS 요청에만)
  • X-Content-Type-Options    — MIME 스니핑 차단
  • Referrer-Policy           — 외부 사이트로 리퍼러 누출 최소화
  • Permissions-Policy        — 카메라/지리/USB 등 불필요 권한 차단
  • X-Frame-Options           — 클릭재킹 방어

예외 경로:
  • /widget, /widget/loader.js — 외부 사이트 iframe 임베드 허용 (frame-ancestors *)
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# 자체 콜센터 화면은 inline script/style 을 다수 사용하므로 'unsafe-inline' 허용.
# 외부 도메인 fetch 는 차단. data: 이미지(아이콘 PNG 등)는 허용.
_CSP_DEFAULT = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'"
)

# 위젯은 외부 사이트 어디서든 iframe 으로 띄울 수 있어야 함
_CSP_WIDGET = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob:; "
    "frame-ancestors *"  # 임베드 허용 핵심
)

_COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), geolocation=(), payment=(), usb=(), "
        "microphone=(self), accelerometer=(), gyroscope=()"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 경로별 CSP / X-Frame-Options 분기
        path = request.url.path
        is_widget = path.startswith("/widget")
        if is_widget:
            response.headers.setdefault("Content-Security-Policy", _CSP_WIDGET)
            # 위젯은 외부에서 iframe 가능 — X-Frame-Options 설정 X
        else:
            response.headers.setdefault("Content-Security-Policy", _CSP_DEFAULT)
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        # 공통 헤더
        for k, v in _COMMON_HEADERS.items():
            response.headers.setdefault(k, v)

        # HSTS — HTTPS 요청에만 (HTTP 에 설정하면 의미 없고 일부 검사기는 경고)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response
