#!/usr/bin/env bash
# AI 콜센터 — 서비스 정지.
#
# 사용법:
#   sudo bash /data/projects/ai-callcenter/scripts/stop.sh

set -euo pipefail

SERVICE=${SERVICE:-ai-callcenter}

log()  { echo -e "\033[1;34m[stop]\033[0m $*"; }
err()  { echo -e "\033[1;31m[stop]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다. sudo 로 실행하세요."
    exit 1
fi

if ! systemctl is-active --quiet "$SERVICE"; then
    log "이미 정지 상태입니다."
    exit 0
fi

log "systemctl stop $SERVICE"
systemctl stop "$SERVICE"

# 완전 정지 대기
for i in {1..20}; do
    if ! systemctl is-active --quiet "$SERVICE"; then
        log "정지 완료"
        exit 0
    fi
    sleep 0.5
done

err "정지 확인 실패 — 강제 종료 시도"
systemctl kill --signal=SIGTERM "$SERVICE" || true
sleep 2
systemctl status "$SERVICE" --no-pager --lines=3 || true
