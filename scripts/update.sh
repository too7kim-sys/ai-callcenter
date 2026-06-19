#!/usr/bin/env bash
# AI 콜센터 — 운영 서버 업데이트 (git pull → 의존성 동기화 → 재시작).
#
# 사용법:
#   sudo bash /data/projects/ai-callcenter/scripts/update.sh
#
# 동작:
#   1. git fetch + (clean working tree 일 때만) pull
#   2. requirements.txt 가 바뀌었으면 pip install
#   3. systemctl restart ai-callcenter
#   4. /healthz 가 200 응답할 때까지 대기

set -euo pipefail

APP_USER=${APP_USER:-ai-callcenter}
APP_DIR=${APP_DIR:-/data/projects/ai-callcenter}
# 미지정 시 현재 체크아웃된 브랜치 사용
APP_BRANCH=${APP_BRANCH:-$(git -C "${APP_DIR}" symbolic-ref --short HEAD 2>/dev/null || echo main)}

log()  { echo -e "\033[1;34m[update]\033[0m $*"; }
warn() { echo -e "\033[1;33m[update]\033[0m $*"; }
err()  { echo -e "\033[1;31m[update]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다."
    exit 1
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
    err "$APP_DIR 에 git 저장소가 없습니다. 먼저 install.sh 를 실행하세요."
    exit 1
fi

# 변경 전 커밋 해시 기록 — 의존성 변경 여부 판단
OLD_REQ_HASH=$(sha256sum "$APP_DIR/requirements.txt" | awk '{print $1}')

log "git fetch + pull (branch=$APP_BRANCH)"
sudo -u "$APP_USER" -H git -C "$APP_DIR" fetch --quiet --tags origin
# 워킹 트리가 dirty 면 멈춤
if ! sudo -u "$APP_USER" -H git -C "$APP_DIR" diff --quiet HEAD; then
    err "워킹 트리에 커밋되지 않은 변경이 있습니다. 직접 처리 후 재시도하세요."
    exit 1
fi
sudo -u "$APP_USER" -H git -C "$APP_DIR" checkout --quiet "$APP_BRANCH"
sudo -u "$APP_USER" -H git -C "$APP_DIR" pull --quiet --ff-only origin "$APP_BRANCH"

NEW_REQ_HASH=$(sha256sum "$APP_DIR/requirements.txt" | awk '{print $1}')
if [[ "$OLD_REQ_HASH" != "$NEW_REQ_HASH" ]]; then
    log "requirements.txt 변경 — pip 동기화"
    sudo -u "$APP_USER" -H "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
else
    log "의존성 변경 없음"
fi

log "systemd 재시작"
systemctl restart ai-callcenter

# 헬스체크
log "헬스체크 대기…"
for i in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        log "OK — /healthz 200"
        echo
        log "최근 로그 5줄:"
        journalctl -u ai-callcenter -n 5 --no-pager
        exit 0
    fi
    sleep 0.5
done

err "헬스체크 실패 — 로그 확인:"
journalctl -u ai-callcenter -n 30 --no-pager
exit 1
