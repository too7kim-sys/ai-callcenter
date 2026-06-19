"""IP 화이트리스트 미들웨어.

운영에서 nginx 가 별도 서버에 있고 백엔드 uvicorn 이 0.0.0.0 으로 바인딩
될 때, 신뢰할 수 있는 IP/네트워크에서만 접근하도록 차단.

환경변수:
  ALLOWED_IPS — 콤마 구분, CIDR 지원. 빈 값이면 비활성(모두 허용).
                예) "10.0.0.0/24, 192.168.1.100, 203.0.113.5/32, 2001:db8::/32"
                "*" 면 명시적으로 비활성과 동일 처리.

예외 경로:
  /healthz, /readyz — 외부 LB / 모니터링이 접근해야 하므로 항상 허용.

클라이언트 IP 판별:
  app.ratelimit.client_ip(request) 재사용 — X-Forwarded-For 첫 hop 우선.
  uvicorn 의 --forwarded-allow-ips 로 신뢰할 프록시 IP 를 미리 지정해야
  request.client.host 가 실제 클라이언트 IP 로 재작성된다.

차단된 요청:
  HTTP 403 + 짧은 JSON 메시지. 로그에는 차단된 IP·경로 기록.
"""
from __future__ import annotations

import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import ratelimit

logger = logging.getLogger("ai_callcenter.ip_allowlist")

# 헬스체크는 항상 허용 (LB·외부 모니터링 핑)
_BYPASS_PATHS = {"/healthz", "/readyz"}

_networks: list[ipaddress._BaseNetwork] = []
_enabled = False
_raw = ""


def configure(allowed_ips_csv: str | None) -> None:
    """ALLOWED_IPS 문자열을 파싱해 미들웨어 상태 초기화.

    잘못된 항목은 로그 경고만 남기고 건너뜀 (다른 항목으로 계속 동작).
    """
    global _networks, _enabled, _raw
    _raw = (allowed_ips_csv or "").strip()
    _networks = []
    if not _raw:
        _enabled = False
        return
    if _raw == "*":
        _enabled = False
        logger.info("ALLOWED_IPS=* — 모든 IP 허용 (비활성과 동일)")
        return
    for token in _raw.split(","):
        t = token.strip()
        if not t:
            continue
        try:
            net = ipaddress.ip_network(t, strict=False)
            _networks.append(net)
        except ValueError as e:
            logger.error("ALLOWED_IPS 잘못된 항목 %r — 무시: %s", t, e)
    _enabled = len(_networks) > 0
    if _enabled:
        logger.info(
            "IP 화이트리스트 활성 — %d개 항목: %s",
            len(_networks), ", ".join(str(n) for n in _networks),
        )


def is_allowed(ip_str: str | None) -> bool:
    """현재 설정 기준 IP 허용 여부."""
    if not _enabled:
        return True
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in _networks:
        if ip in net:
            return True
    return False


def status() -> dict:
    return {
        "enabled": _enabled,
        "raw": _raw,
        "ranges": [str(n) for n in _networks],
        "bypass_paths": sorted(_BYPASS_PATHS),
    }


class IpAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _enabled or request.url.path in _BYPASS_PATHS:
            return await call_next(request)
        client = ratelimit.client_ip(request)
        if is_allowed(client):
            return await call_next(request)
        logger.warning(
            "IP 차단 ip=%s method=%s path=%s",
            client, request.method, request.url.path,
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "이 IP 에서는 접근할 수 없습니다."},
        )
