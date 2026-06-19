#!/usr/bin/env bash
# AI 콜센터 — 운영 서버 1회 설치 스크립트 (Ubuntu/Debian).
#
# 가정:
#   • nginx 는 별도 서버에 이미 운영 중 → 여기선 설치하지 않음
#   • 이 백엔드 서버는 nginx 서버에서만 접근 (ALLOWED_IPS 로 IP 제한)
#
# 자동 수행:
#   1. 시스템 패키지 (python3, ffmpeg, espeak-ng) 설치
#   2. 전용 사용자 ai-callcenter 생성
#   3. /data/projects/ai-callcenter 에 git clone (있으면 pull)
#   4. .venv 생성 + 의존성 설치
#   5. .env 가 없으면 deploy/env.production.example 복사 + 랜덤 admin 비번
#   6. systemd 유닛 설치 + 시작
#   7. nginx 서버 IP 입력 안내 (ALLOWED_IPS / TRUSTED_PROXY_IPS)
#
# 멱등 — 안전하게 재실행 가능.
#
# 사용법:
#   sudo bash scripts/install.sh [<git-url>]
#   (git-url 미지정 시 origin 이 잡힌 현재 디렉토리 사용)

set -euo pipefail

APP_USER=${APP_USER:-ai-callcenter}
APP_DIR=${APP_DIR:-/data/projects/ai-callcenter}
APP_BRANCH=${APP_BRANCH:-main}
PYTHON=${PYTHON:-python3}
GIT_URL=${1:-}

log()  { echo -e "\033[1;34m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[install]\033[0m $*"; }
err()  { echo -e "\033[1;31m[install]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다. sudo 로 실행해 주세요."
    exit 1
fi

# ---------- 1. 시스템 패키지 ----------
# nginx 는 별도 서버에서 운영 — 여기서는 설치 안 함.
log "시스템 패키지 확인 (python3-venv, ffmpeg, espeak-ng)…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg espeak-ng \
    git ca-certificates curl >/dev/null 2>&1 \
    || warn "일부 패키지 설치 실패 — 이미 설치돼 있으면 무시"

# ---------- 2. 사용자 + 디렉토리 ----------
if ! id "$APP_USER" >/dev/null 2>&1; then
    log "사용자 $APP_USER 생성"
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR"
mkdir -p "$(dirname "$APP_DIR")"
chown -R "$APP_USER:$APP_USER" "$(dirname "$APP_DIR")" 2>/dev/null || true

# ---------- 3. 소스 코드 ----------
if [[ -d "$APP_DIR/.git" ]]; then
    log "기존 저장소 발견 — git pull"
    sudo -u "$APP_USER" -H git -C "$APP_DIR" fetch --quiet origin
    sudo -u "$APP_USER" -H git -C "$APP_DIR" checkout --quiet "$APP_BRANCH"
    sudo -u "$APP_USER" -H git -C "$APP_DIR" pull --quiet --ff-only origin "$APP_BRANCH"
elif [[ -n "$GIT_URL" ]]; then
    log "git clone $GIT_URL → $APP_DIR"
    sudo -u "$APP_USER" -H git clone --branch "$APP_BRANCH" --depth 50 "$GIT_URL" "$APP_DIR"
else
    # 현재 디렉토리가 git 저장소면 그것을 origin 으로
    CURRENT=$(pwd)
    if [[ -d "$CURRENT/.git" ]]; then
        REMOTE=$(git -C "$CURRENT" remote get-url origin 2>/dev/null || true)
        if [[ -n "$REMOTE" ]]; then
            log "현재 origin($REMOTE) 으로 clone"
            sudo -u "$APP_USER" -H git clone --branch "$APP_BRANCH" --depth 50 "$REMOTE" "$APP_DIR"
        else
            err "git URL 을 인수로 지정하거나, 이미 clone 된 디렉토리에서 실행해 주세요."
            exit 1
        fi
    else
        err "git URL 을 첫 번째 인수로 지정해 주세요: sudo bash scripts/install.sh https://…"
        exit 1
    fi
fi

# ---------- 4. Python 가상환경 + 의존성 ----------
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    log "Python venv 생성"
    sudo -u "$APP_USER" -H "$PYTHON" -m venv "$APP_DIR/.venv"
fi
log "Python 의존성 설치"
sudo -u "$APP_USER" -H "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" -H "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ---------- 5. .env ----------
if [[ ! -f "$APP_DIR/.env" ]]; then
    log ".env 생성 (강한 랜덤 admin 비밀번호)"
    cp "$APP_DIR/deploy/env.production.example" "$APP_DIR/.env"
    RANDOM_PW=$(tr -dc 'A-Za-z0-9!@#%^&*' </dev/urandom | head -c 24)
    sed -i "s/__CHANGE_ME__/$RANDOM_PW/" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo
    echo "  ┌─────────────────────────────────────────────────────────────┐"
    echo "  │  초기 admin 비밀번호 (이 화면을 닫으면 다시 표시되지 않음)    │"
    echo "  │  username: admin                                            │"
    echo "  │  password: $RANDOM_PW"
    echo "  │  → 로그인 후 즉시 /security 에서 2FA 활성화 권장             │"
    echo "  └─────────────────────────────────────────────────────────────┘"
    echo
else
    log ".env 가 이미 존재 — 변경하지 않음"
fi

# ---------- 6. 디렉토리 권한 ----------
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ---------- 7. systemd ----------
log "systemd 유닛 설치"
install -m 644 "$APP_DIR/deploy/ai-callcenter.service" /etc/systemd/system/ai-callcenter.service
systemctl daemon-reload
systemctl enable --quiet ai-callcenter
systemctl restart ai-callcenter

# 부팅 대기
for i in {1..20}; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        log "서버 응답 정상 (/healthz 200)"
        break
    fi
    sleep 0.5
done

# ---------- 8. 마무리 안내 ----------
echo
log "설치 완료"
cat <<EOF

  서비스 관리:
    systemctl status  ai-callcenter
    systemctl restart ai-callcenter
    journalctl -u ai-callcenter -f

  업데이트:
    sudo bash $APP_DIR/scripts/update.sh

  ┌─────────────────────────────────────────────────────────────────┐
  │  ⚠  반드시 설정해야 할 항목 ($APP_DIR/.env)
  │
  │  1) ALLOWED_IPS       — nginx 서버 IP + 사내망 등 허용 IP/CIDR
  │     예: ALLOWED_IPS=10.0.0.5/32,192.168.0.0/24
  │     빈 값이면 누구나 접근 가능 — 운영에선 반드시 설정!
  │
  │  2) TRUSTED_PROXY_IPS — X-Forwarded-For 신뢰할 프록시 IP (nginx 서버)
  │     예: TRUSTED_PROXY_IPS=10.0.0.5
  │     이 값이 정확해야 ALLOWED_IPS 가 실제 클라이언트 IP 를 평가함
  │
  │  3) APP_BASE_URL      — 외부에 노출되는 도메인 (https 포함)
  │     예: APP_BASE_URL=https://callcenter.example.com
  │
  │  편집 후: sudo systemctl restart ai-callcenter
  └─────────────────────────────────────────────────────────────────┘

  nginx (별도 서버) 설정 참고:
    이 저장소의 deploy/nginx.conf.example 을 nginx 서버에 복사·수정.
    핵심: proxy_pass http://<백엔드-서버-IP>:8000;
          SSE 경로(/api/events/stream)는 proxy_buffering off + 24h timeout.

  앱 상태 확인:
    curl -fsS http://127.0.0.1:8000/healthz    # 항상 200 (IP 검사 우회)
    curl -fsS http://127.0.0.1:8000/readyz     # DB 까지 ping

  DB 백업 위치: $APP_DIR/backups/
  로그:          journalctl -u ai-callcenter

EOF
