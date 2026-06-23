#!/usr/bin/env bash
# AI 콜센터 — admin 비밀번호 재설정 (잠금/2FA 함께 해제 가능).
#
# 사용법:
#   sudo bash scripts/reset-admin-password.sh                    # 대화식 (안전)
#   sudo bash scripts/reset-admin-password.sh --user cusoft      # 다른 사용자명
#   sudo bash scripts/reset-admin-password.sh --disable-2fa      # 2FA 도 같이 해제
#
# 동작:
#   1. 새 비밀번호 입력 (보이지 않음, 2회 확인)
#   2. AgentUser.password_hash 재설정
#   3. failed_login_count=0, locked_until=NULL, active=True 로 정리
#   4. --disable-2fa 시 totp_enabled=False + secret/recovery 제거

set -euo pipefail

APP_DIR=${APP_DIR:-/data/projects/ai-callcenter}
APP_USER=${APP_USER:-$(stat -c '%U' "$APP_DIR" 2>/dev/null || echo ai-callcenter)}
USERNAME=admin
DISABLE_2FA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user|-u)        USERNAME="$2"; shift 2 ;;
        --disable-2fa)    DISABLE_2FA=1; shift ;;
        --password)       NEW_PW="$2"; shift 2 ;;  # 비대화식 (CI 등) — 권장 X
        -h|--help)
            sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "root 권한이 필요합니다." >&2
    exit 1
fi
if [[ ! -d "$APP_DIR/.venv" ]]; then
    echo "$APP_DIR/.venv 없음 — install.sh 먼저 실행하세요." >&2
    exit 1
fi

# 대화식 비번 입력
if [[ -z "${NEW_PW:-}" ]]; then
    read -rsp "새 비밀번호 (8자 이상): " NEW_PW; echo
    read -rsp "한 번 더 확인:             " CONFIRM; echo
    if [[ "$NEW_PW" != "$CONFIRM" ]]; then
        echo "✗ 비밀번호가 일치하지 않습니다." >&2
        exit 1
    fi
fi
if [[ ${#NEW_PW} -lt 8 ]]; then
    echo "✗ 비밀번호는 최소 8자 이상이어야 합니다." >&2
    exit 1
fi

sudo -u "$APP_USER" -H bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' - <<PYEOF
import sys
sys.path.insert(0, '$APP_DIR')
from app.database import SessionLocal
from app.models import AgentUser
from app import security

db = SessionLocal()
u = db.query(AgentUser).filter_by(username='$USERNAME').first()
if u is None:
    print('✗ 사용자 \\\"$USERNAME\\\" 를 찾을 수 없습니다.')
    sys.exit(1)

u.password_hash = security.hash_password('''$NEW_PW''')
u.failed_login_count = 0
u.locked_until = None
u.active = True
if $DISABLE_2FA:
    u.totp_enabled = False
    u.totp_secret = None
    u.totp_recovery = None
db.commit()
print(f'✓ 사용자 \\\"$USERNAME\\\" 비밀번호 재설정 완료')
print(f'  active=True, failed_count=0, locked_until=None')
print(f'  2FA: {\"비활성화 (방금 해제)\" if $DISABLE_2FA else (\"활성 — 인증기 코드 필요\" if u.totp_enabled else \"미설정\")}')
PYEOF
"
