#!/usr/bin/env bash
# AI 콜센터 — 상태 + 최근 로그 한눈에.
#
# 사용법:
#   bash /data/projects/ai-callcenter/scripts/status.sh

set -uo pipefail

SERVICE=${SERVICE:-ai-callcenter}

echo "=== 서비스 ==="
systemctl status "$SERVICE" --no-pager --lines=5 || true

echo
echo "=== 헬스체크 ==="
echo -n "  /healthz: "
curl -fsS http://127.0.0.1:8000/healthz 2>&1 || echo "응답 없음"
echo
echo -n "  /readyz : "
curl -fsS http://127.0.0.1:8000/readyz 2>&1 || echo "응답 없음"
echo

echo
echo "=== 리스닝 포트 ==="
ss -tlnp 2>/dev/null | grep -E ':(8000|80|443)\s' || echo "  8000 포트 리스닝 없음"

echo
echo "=== 최근 로그 (10줄) ==="
journalctl -u "$SERVICE" -n 10 --no-pager 2>/dev/null || true
