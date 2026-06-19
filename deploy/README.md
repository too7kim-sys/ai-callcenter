# AI 콜센터 — 운영 서버 배포

대상 경로: `/data/projects/ai-callcenter/`

**가정**: nginx 는 **별도 서버**에서 이미 운영 중. 이 백엔드 서버는 nginx 서버에서만 접근 가능하도록 `ALLOWED_IPS` 로 IP 제한.

```
[ Internet ] ─► [ 별도 nginx 서버 ] ─► [ 이 백엔드 ai-callcenter:8000 ]
                                       ALLOWED_IPS 로 nginx 서버 IP 만 허용
```

## 빠른 시작 (Ubuntu 20.04 / 22.04 / 24.04)

```bash
# 1. 첫 설치 (한 줄)
curl -fsSL https://raw.githubusercontent.com/too7kim-sys/ai-callcenter/main/scripts/install.sh \
  | sudo bash -s -- https://github.com/too7kim-sys/ai-callcenter.git

# 또는 이미 clone 한 디렉토리에서
sudo bash scripts/install.sh https://github.com/too7kim-sys/ai-callcenter.git
```

설치 후 화면에 출력되는 **초기 admin 비밀번호**를 기록하세요. 이후 표시되지 않습니다.

설치 직후 반드시 `.env` 의 `ALLOWED_IPS`·`TRUSTED_PROXY_IPS`·`APP_BASE_URL` 채우고 재시작:
```bash
sudo -u ai-callcenter $EDITOR /data/projects/ai-callcenter/.env
sudo systemctl restart ai-callcenter
```

## 배포되는 것들

| 위치 | 용도 |
| --- | --- |
| `/data/projects/ai-callcenter/` | 앱 소스 + `.venv` + DB + 백업 |
| `/etc/systemd/system/ai-callcenter.service` | 자동 시작·재시작 |
| `/data/projects/ai-callcenter/.env` | 운영 설정 (gitignored) |
| 사용자 `ai-callcenter` | 앱 전용 계정 (셸 X) |
| 포트 `127.0.0.1:8000` | nginx 가 프록시할 백엔드 |

## 흐름

```
Internet ─► nginx (443/HTTPS) ─► uvicorn (127.0.0.1:8000) ─► FastAPI
                                                            ├─ SQLite (callcenter.db)
                                                            ├─ backups/ (자동 백업)
                                                            └─ Whisper / Edge TTS
```

## 일상 운영

### 서비스 제어 (간편 스크립트)
```bash
cd /data/projects/ai-callcenter

sudo bash scripts/start.sh      # 시작 + /healthz 대기
sudo bash scripts/stop.sh       # 정지
sudo bash scripts/restart.sh    # 재시작 + /healthz 대기
bash      scripts/status.sh     # 서비스 + 헬스 + 포트 + 최근 로그 한눈에
sudo bash scripts/update.sh     # GitHub pull → 의존성 동기화 → 재시작
```

내부 동작은 모두 `systemctl <action> ai-callcenter` 의 얇은 래퍼 + 헬스체크 + 친절한 출력.

### 업데이트 자세히
`scripts/update.sh` 가 하는 일:
1. `git fetch origin <현재 브랜치>` — 로컬 브랜치 자동 인식
2. 워킹 트리 dirty 면 중단 (예상치 못한 변경 보호)
3. 새 커밋이 없으면 재시작도 하지 않고 종료 (no-op)
4. `git pull --ff-only`
5. 받은 새 커밋 목록을 화면에 출력
6. `requirements.txt` 가 바뀌었을 때만 `pip install`
7. `systemctl restart` + `/healthz` 대기 (최대 15초)

### 상태 확인 (저수준)
```bash
systemctl status ai-callcenter
journalctl -u ai-callcenter -f      # 실시간 로그
journalctl -u ai-callcenter --since "1 hour ago"
curl -fsS http://127.0.0.1:8000/healthz   # liveness
curl -fsS http://127.0.0.1:8000/readyz    # DB 까지 ping
```

### 설정 변경
```bash
sudo -u ai-callcenter $EDITOR /data/projects/ai-callcenter/.env
sudo systemctl restart ai-callcenter
```

### 백업 / 복원
- 자동 백업: 매 24h, `backups/callcenter-YYYYMMDD-HHMMSS.db.gz` (30일 보존, `.env` 에서 조정 가능)
- 수동 백업 (관리자 로그인 후): `POST /api/admin/backup/run`
- 복원:
  ```bash
  sudo systemctl stop ai-callcenter
  cd /data/projects/ai-callcenter
  gunzip -c backups/callcenter-YYYYMMDD-HHMMSS.db.gz > callcenter.db
  sudo systemctl start ai-callcenter
  ```

### 메트릭 / 캐시
- `/metrics` 페이지 — 엔드포인트별 p50/p95/p99, 캐시 hit rate
- `/api/admin/metrics` (JSON)
- `/api/admin/cache/clear` — 캐시 강제 비우기

## 네트워크 차단 (IP 화이트리스트)

이 백엔드는 외부망에 노출되지 않게 두 단계로 잠급니다.

**1단계 — 앱 레벨 (이 저장소)**: `.env` 의 `ALLOWED_IPS` 에 nginx 서버 IP + 사내망 등 허용 IP/CIDR 만 나열. 빈 값이면 누구나 접근 가능하므로 운영에선 반드시 채울 것.

```ini
# .env
BIND_HOST=0.0.0.0
BIND_PORT=8000

# nginx 서버 IP (다중이면 콤마)
TRUSTED_PROXY_IPS=10.0.0.5

# 허용 IP/CIDR — nginx 서버 + 사내 점검용 등
ALLOWED_IPS=10.0.0.5/32,192.168.0.0/24
```

설정 확인 (admin 로그인 후):
```
GET /api/admin/security/ip_allowlist
```

**2단계 — OS 방화벽 (강력 권장)**: 같은 IP 만 8000 포트로 들어오도록 OS 가 차단:
```bash
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw allow from 10.0.0.5 to any port 8000   # nginx 서버
sudo ufw allow from 192.168.0.0/24 to any port 8000  # 사내망 점검
sudo ufw enable
```

`/healthz`, `/readyz` 는 IP 화이트리스트의 영향을 받지 않으므로 외부 모니터링 도구도 정상 동작합니다.

## nginx (별도 서버) 설정 참고

이 저장소의 `deploy/nginx.conf.example` 을 **별도 nginx 서버**로 복사·수정해 사용합니다 (이 백엔드 서버에는 nginx 설치 X).

```bash
# nginx 서버에서:
sudo cp /tmp/nginx.conf.example /etc/nginx/sites-available/ai-callcenter

# upstream 대상을 이 백엔드 서버의 내부 IP 로:
sudo sed -i 's|server 127.0.0.1:8000;|server <백엔드-IP>:8000;|' \
        /etc/nginx/sites-available/ai-callcenter
sudo sed -i 's/callcenter.example.com/your-domain.com/g' \
        /etc/nginx/sites-available/ai-callcenter

sudo ln -sf /etc/nginx/sites-available/ai-callcenter /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Let's Encrypt 인증서:
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

WebRTC 통화·STT·PWA 설치는 모두 **HTTPS 필수**이므로 nginx 서버에서 반드시 인증서 발급.

설정 후 백엔드의 `.env` 의 `APP_BASE_URL=https://your-domain.com` 갱신 → `systemctl restart`.

## 보안 체크리스트

설치 직후:

- [ ] 초기 admin 비밀번호로 로그인 → `/security` 에서 **변경**
- [ ] **2FA 활성화** (Google Authenticator 등록)
- [ ] `.env` 의 `ALLOWED_IPS` + `TRUSTED_PROXY_IPS` 채움 (필수!)
- [ ] `.env` 의 `ANTHROPIC_API_KEY` (사용 시) / `SMTP_PASSWORD` / `HF_TOKEN` 설정
- [ ] `ALERT_WEBHOOK_URL` 에 Slack 등 채널 연결
- [ ] nginx 서버에서 HTTPS 활성화 + 백엔드 `.env` 의 `APP_BASE_URL` 업데이트
- [ ] OS 방화벽 (`ufw`) 로 8000 포트도 nginx 서버 IP 만 허용
- [ ] 자동 백업이 동작하는지 1일 후 `backups/` 확인
- [ ] `GET /api/admin/security/ip_allowlist` 로 화이트리스트 설정 검증

## 멀티 워커 / 수평 확장 (선택)

기본 설정은 **단일 워커** 입니다. 이유:
- 인메모리 상태: TTL 캐시, SSE 브로커, 이상 활동 추적, 레이트 리미트, 메트릭
- SQLite: 멀티 프로세스에서 락 경합

QPS 가 단일 코어 한계를 넘으면:
1. **PostgreSQL 로 이전** (`DATABASE_URL=postgresql://…`)
2. **Redis 로 전역 상태 이전** (캐시·레이트리미트·이상감지를 redis 백엔드로)
3. systemd `--workers 4` 또는 gunicorn+uvicorn-worker
4. 또는 컨테이너화 후 Kubernetes

지금 규모(상담원 ≤ 50명, 동시 통화 ≤ 30건)에선 단일 워커로 충분합니다.

## 문제 해결

| 증상 | 확인 |
| --- | --- |
| `systemctl status` 에 `failed` | `journalctl -u ai-callcenter -n 50` 첫 에러 줄 |
| HTTP 502 (nginx) | `systemctl status ai-callcenter` + `curl 127.0.0.1:8000/healthz` |
| WebRTC 통화 안 됨 | HTTPS 여부 확인 (`http://` 면 마이크 차단). 사내망 깊은 NAT 면 TURN 필요 |
| 통화 시그널이 안 옴 | nginx 의 `/api/events/stream` 위치에 `proxy_buffering off` 확인 |
| STT 503 | `journalctl` 에서 Whisper 다운로드 실패 메시지. `HF_TOKEN` 설정 권장 |
| 백업 안 됨 | `.env` 의 `BACKUP_ENABLED=true`? `backups/` 쓰기 권한? `ai-callcenter` 소유? |
| 비밀번호 재설정 이메일 안 옴 | `SMTP_HOST` 비어있으면 로그로 폴백됨. `journalctl` 확인 |

## 제거

```bash
sudo systemctl stop ai-callcenter
sudo systemctl disable ai-callcenter
sudo rm /etc/systemd/system/ai-callcenter.service
sudo systemctl daemon-reload
sudo rm -rf /data/projects/ai-callcenter   # ⚠ DB·백업 모두 사라짐
sudo userdel -r ai-callcenter             # 홈디렉토리도 삭제
sudo rm /etc/nginx/sites-enabled/ai-callcenter
sudo systemctl reload nginx
```
