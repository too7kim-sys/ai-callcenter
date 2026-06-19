#!/usr/bin/env bash
# AI 콜센터 — 서비스 재시작 (코드 변경 없이 .env 갱신 등 적용).
#
# 사용법:
#   sudo bash /data/projects/ai-callcenter/scripts/restart.sh

set -euo pipefail

SERVICE=${SERVICE:-ai-callcenter}

log()  { echo -e "\033[1;34m[restart]\033[0m $*"; }
err()  { echo -e "\033[1;31m[restart]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다. sudo 로 실행하세요."
    exit 1
fi

log "systemctl restart $SERVICE"
systemctl restart "$SERVICE"

# 헬스체크 대기 (최대 15초)
for i in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        log "정상 응답 (/healthz 200)"
        echo
        systemctl status "$SERVICE" --no-pager --lines=5 || true
        exit 0
    fi
    sleep 0.5
done

err "헬스체크 실패 — 로그 확인:"
journalctl -u "$SERVICE" -n 30 --no-pager
exit 1
