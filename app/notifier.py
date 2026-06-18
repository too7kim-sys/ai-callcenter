"""외부 알림 (Slack / Teams / Discord / 사용자 정의 Webhook).

`ALERT_WEBHOOK_URL` 환경변수가 설정되어 있으면 위험 상담·에스컬레이션 등
중요 이벤트를 외부 채널로 푸시한다. 미설정 시 no-op 으로 동작한다.

설계 원칙:
  • Fire-and-forget — 알림 실패가 본 요청 흐름을 막지 않는다.
  • Slack Incoming Webhook 호환 JSON — Teams/Discord 도 동일 URL 로 동작.
  • 표준 라이브러리만 사용 — 추가 패키지 불필요.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from . import config

logger = logging.getLogger("ai_callcenter.notifier")

_TIMEOUT_SECS = 5

_SEVERITY_ICON = {
    "info":     ":information_source:",
    "warning":  ":warning:",
    "critical": ":rotating_light:",
}


def is_configured() -> bool:
    return bool(getattr(config, "ALERT_WEBHOOK_URL", ""))


def notify_escalation(
    *,
    conversation_id: int,
    customer_name: str,
    reason: str,
    risk_level: str | None = None,
    last_message: str | None = None,
) -> None:
    """상담이 에스컬레이션 상태로 전환됐을 때 알림."""
    sev = "critical" if risk_level == "high" else "warning"
    lines = [
        f"*상담 #{conversation_id}* — {reason}",
        f"> 고객: *{customer_name}*",
    ]
    if risk_level:
        lines.append(f"> 위험도: *{risk_level}*")
    if last_message:
        snippet = last_message.strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        lines.append(f"> 메시지: \"{snippet}\"")
    notify(
        title="고위험 상담 발생" if sev == "critical" else "상담 에스컬레이션",
        body="\n".join(lines),
        severity=sev,
        conversation_id=conversation_id,
    )


def notify(
    *,
    title: str,
    body: str,
    severity: str = "info",
    conversation_id: int | None = None,
) -> None:
    """일반 알림 (fire-and-forget). 호출 즉시 반환."""
    if not is_configured():
        return
    payload = _build_payload(title, body, severity, conversation_id)
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def send_test() -> dict:
    """동기 테스트 — 관리자가 웹훅 URL 설정을 검증할 수 있게 한다.

    반환: {ok, status, error?} — 실패해도 예외를 던지지 않는다.
    """
    if not is_configured():
        return {"ok": False, "error": "ALERT_WEBHOOK_URL 이 설정되어 있지 않습니다."}
    payload = _build_payload(
        title="AI 콜센터 알림 테스트",
        body="이 메시지가 보이면 외부 알림 설정이 정상입니다. :white_check_mark:",
        severity="info",
        conversation_id=None,
    )
    return _post(payload)


# --------------------------------------------------------------------

def _build_payload(title: str, body: str, severity: str, conversation_id: int | None) -> dict:
    icon = _SEVERITY_ICON.get(severity, ":bell:")
    text = f"{icon} *{title}*\n{body}"
    link = _conversation_link(conversation_id)
    if link:
        text += f"\n<{link}|상담원 콘솔에서 열기 →>"
    return {"text": text}


def _conversation_link(conversation_id: int | None) -> str | None:
    if conversation_id is None:
        return None
    base = (getattr(config, "APP_BASE_URL", "") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/agent?conv={conversation_id}"


def _post(payload: dict) -> dict:
    url = config.ALERT_WEBHOOK_URL
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECS) as resp:
            status = resp.status
            if 200 <= status < 300:
                return {"ok": True, "status": status}
            return {"ok": False, "status": status, "error": f"HTTP {status}"}
    except urllib.error.HTTPError as exc:
        logger.warning("외부 알림 실패: HTTP %s — %s", exc.code, exc.reason)
        return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code} {exc.reason}"}
    except Exception as exc:
        logger.warning("외부 알림 실패: %s", exc)
        return {"ok": False, "error": str(exc)}
