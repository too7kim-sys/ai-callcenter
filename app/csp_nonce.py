"""CSP nonce 자동 주입 미들웨어 — inline <script>/<style> XSS 격리.

설계:
  1) 요청마다 nonce 생성 (secrets.token_urlsafe(16))
  2) text/html 응답 body 를 읽어 <script>/<style> 태그에 nonce="..." 자동 삽입
     • src 속성이 있는 <script src="..."> 는 nonce 불필요 — 건드리지 않음
     • 인라인 <script>...</script> / <style>...</style> 만 대상
  3) CSP 헤더에서 'unsafe-inline' 을 nonce 로 교체:
     script-src 'self' 'nonce-XYZ' 'strict-dynamic'
     style-src  'self' 'nonce-XYZ' 'unsafe-inline'   ← 스타일은 하위호환 유지

효과:
  • XSS 발견 시 공격자가 삽입한 <script> 는 nonce 가 없어 실행 안 됨
  • 이미 렌더된 신뢰 스크립트만 실행 (모든 정규 스크립트에 서버가 nonce 부여)
  • 'strict-dynamic' 으로 nonced 스크립트가 로드한 후속 스크립트도 허용

주의:
  • 인라인 이벤트 핸들러(onclick 등) 는 nonce 로 커버 불가.
    HTML 에서 addEventListener 로 리팩토링 필수.
  • widget 페이지는 외부 임베드 대상 — frame-ancestors * 유지 (별도 CSP)
"""
from __future__ import annotations

import re
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# src 속성이 없는 <script> 만 매칭 (인라인)
_INLINE_SCRIPT_RE = re.compile(
    rb'<script(?![^>]*\bsrc\s*=)([^>]*)>',
    re.IGNORECASE,
)
_INLINE_STYLE_RE = re.compile(rb'<style([^>]*)>', re.IGNORECASE)


def _inject_nonce(body: bytes, nonce: str) -> bytes:
    n = f' nonce="{nonce}"'.encode()

    def add_script(m: re.Match) -> bytes:
        # 이미 nonce= 있으면 그대로
        attrs = m.group(1)
        if b" nonce=" in attrs or b'\tnonce=' in attrs:
            return m.group(0)
        return b"<script" + attrs + n + b">"

    def add_style(m: re.Match) -> bytes:
        attrs = m.group(1)
        if b" nonce=" in attrs or b'\tnonce=' in attrs:
            return m.group(0)
        return b"<style" + attrs + n + b">"

    body = _INLINE_SCRIPT_RE.sub(add_script, body)
    body = _INLINE_STYLE_RE.sub(add_style, body)
    return body


def _update_csp_with_nonce(csp: str, nonce: str) -> str:
    """CSP 헤더 문자열에서 'unsafe-inline' 을 nonce 로 교체."""
    n = f"'nonce-{nonce}'"
    # script-src 는 'unsafe-inline' 제거하고 nonce + strict-dynamic 추가
    csp = re.sub(
        r"script-src ([^;]+)",
        lambda m: "script-src " + _replace_unsafe_inline(m.group(1), n, add_dynamic=True),
        csp,
    )
    # style-src 는 하위호환 위해 'unsafe-inline' 유지 + nonce 추가
    csp = re.sub(
        r"style-src ([^;]+)",
        lambda m: "style-src " + _replace_unsafe_inline(m.group(1), n, add_dynamic=False, keep_unsafe=True),
        csp,
    )
    return csp


def _replace_unsafe_inline(directive_value: str, nonce_token: str,
                           add_dynamic: bool, keep_unsafe: bool = False) -> str:
    parts = directive_value.strip().split()
    out: list[str] = []
    seen_unsafe = False
    for p in parts:
        if p == "'unsafe-inline'":
            seen_unsafe = True
            if keep_unsafe:
                out.append(p)
            # else: 제거
        else:
            out.append(p)
    if nonce_token not in out:
        out.append(nonce_token)
    if add_dynamic and "'strict-dynamic'" not in out and seen_unsafe:
        out.append("'strict-dynamic'")
    return " ".join(out)


class CspNonceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return response

        # 응답 body 를 읽어 태그에 nonce 주입
        try:
            body = b""
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                body += chunk
        except Exception:
            return response

        modified = _inject_nonce(body, nonce)

        # CSP 헤더 갱신
        csp = response.headers.get("content-security-policy")
        if csp:
            new_csp = _update_csp_with_nonce(csp, nonce)
            response.headers["content-security-policy"] = new_csp

        headers = dict(response.headers)
        headers.pop("content-length", None)  # body 길이가 바뀜
        return Response(
            content=modified,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type or "text/html; charset=utf-8",
        )
