#!/usr/bin/env bash
# AI 콜센터 — 서비스 시작.
#
# 사용법:
#   sudo bash /data/projects/ai-callcenter/scripts/start.sh

set -euo pipefail

SERVICE=${SERVICE:-ai-callcenter}
APP_DIR=${APP_DIR:-/data/projects/ai-callcenter}

log()  { echo -e "\033[1;34m[start]\033[0m $*"; }
err()  { echo -e "\033[1;31m[start]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다. sudo 로 실행하세요."
    exit 1
fi

if systemctl is-active --quiet "$SERVICE"; then
    log "이미 실행 중입니다."
else
    log "systemctl start $SERVICE"
    systemctl start "$SERVICE"
fi

# 헬스체크 대기 (최대 15초)
for i in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        log "정상 응답 (/healthz 200)"
        break
    fi
    sleep 0.5
done

echo
systemctl status "$SERVICE" --no-pager --lines=5 || true
