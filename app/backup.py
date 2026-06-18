"""SQLite 자동 백업 + 보존 정책.

설정 (.env):
  BACKUP_ENABLED=true|false        기본 true
  BACKUP_DIR=./backups             백업 파일 디렉토리
  BACKUP_INTERVAL_HOURS=24         주기 (시간)
  BACKUP_RETENTION_DAYS=14         보존 기간 (일)

설계:
  • sqlite3.Connection.backup() 사용 — 동시 쓰기에도 안전한 공식 백업 API.
  • gzip 압축 후 callcenter-YYYYMMDD-HHMMSS.db.gz 로 저장.
  • 데몬 스레드가 주기적으로 실행. 시작 시 마지막 백업이 INTERVAL 이상
    경과했으면 즉시 1회 백업 (재시작 직후 데이터 보호).
  • PostgreSQL 등 비-SQLite DB 는 미지원 — 모듈은 no-op 으로 동작.
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config

logger = logging.getLogger("ai_callcenter.backup")

BACKUP_ENABLED = (os.getenv("BACKUP_ENABLED", "true").strip().lower() != "false")
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./backups").strip() or "./backups")
try:
    BACKUP_INTERVAL_HOURS = max(1, int(os.getenv("BACKUP_INTERVAL_HOURS", "24") or 24))
except ValueError:
    BACKUP_INTERVAL_HOURS = 24
try:
    BACKUP_RETENTION_DAYS = max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "14") or 14))
except ValueError:
    BACKUP_RETENTION_DAYS = 14

_FILE_PREFIX = "callcenter-"
_FILE_SUFFIX = ".db.gz"


def is_supported() -> bool:
    return config.DATABASE_URL.startswith("sqlite")


def _db_path() -> str | None:
    """sqlite:///./callcenter.db → ./callcenter.db 추출."""
    url = config.DATABASE_URL
    if not url.startswith("sqlite:"):
        return None
    return url.replace("sqlite:///", "", 1).replace("sqlite://", "", 1)


def backup_now() -> Path:
    """1회 백업 실행 후 백업 파일 경로 반환. 실패 시 예외."""
    src_path = _db_path()
    if src_path is None:
        raise RuntimeError("SQLite 가 아닌 DB는 백업 미지원입니다.")
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"DB 파일이 없습니다: {src_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_path = BACKUP_DIR / f"{_FILE_PREFIX}{ts}.db"
    gz_path = BACKUP_DIR / f"{_FILE_PREFIX}{ts}{_FILE_SUFFIX}"

    # SQLite 공식 백업 API — 동시 쓰기 안전
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(str(raw_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # gzip 압축 후 원본 .db 제거
    with open(raw_path, "rb") as fi, gzip.open(gz_path, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    raw_path.unlink(missing_ok=True)

    logger.info("백업 생성: %s (%.1f MB)", gz_path.name, gz_path.stat().st_size / 1e6)
    return gz_path


def cleanup_old_backups() -> int:
    """보존 기간 지난 백업 파일 삭제. 삭제 개수 반환."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    removed = 0
    for p in BACKUP_DIR.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("오래된 백업 %d개 삭제 (보존 %d일)", removed, BACKUP_RETENTION_DAYS)
    return removed


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    items = []
    for p in sorted(BACKUP_DIR.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"), reverse=True):
        st = p.stat()
        items.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return items


def status() -> dict:
    files = list_backups()
    return {
        "enabled": BACKUP_ENABLED and is_supported(),
        "supported": is_supported(),
        "dir": str(BACKUP_DIR),
        "interval_hours": BACKUP_INTERVAL_HOURS,
        "retention_days": BACKUP_RETENTION_DAYS,
        "backup_count": len(files),
        "latest": files[0] if files else None,
    }


# --------------------------------------------------------------------
# 스케줄러 (시작 시 1회 호출)
# --------------------------------------------------------------------

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_scheduler() -> bool:
    """앱 부트 시 1회 호출. 데몬 스레드로 주기적 백업을 실행한다."""
    global _scheduler_thread
    if not BACKUP_ENABLED:
        logger.info("자동 백업 비활성화 (BACKUP_ENABLED=false)")
        return False
    if not is_supported():
        logger.warning("자동 백업: SQLite 가 아니므로 비활성. DATABASE_URL=%s", config.DATABASE_URL)
        return False
    if _scheduler_thread and _scheduler_thread.is_alive():
        return True
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="backup-scheduler", daemon=True,
    )
    _scheduler_thread.start()
    logger.info(
        "자동 백업 시작 (주기 %d시간, 보존 %d일, 디렉토리 %s)",
        BACKUP_INTERVAL_HOURS, BACKUP_RETENTION_DAYS, BACKUP_DIR,
    )
    return True


def _scheduler_loop():
    """시작 직후 1회 (overdue 일 때) → 이후 INTERVAL 마다."""
    interval_secs = BACKUP_INTERVAL_HOURS * 3600
    try:
        if _is_overdue():
            _safe_run()
    except Exception as exc:
        logger.warning("백업 초기 실행 실패: %s", exc)

    while not _stop_event.wait(interval_secs):
        _safe_run()


def _safe_run():
    try:
        backup_now()
        cleanup_old_backups()
    except Exception as exc:
        logger.warning("자동 백업 실패: %s", exc)


def _is_overdue() -> bool:
    files = list_backups()
    if not files:
        return True
    try:
        latest_mtime = (BACKUP_DIR / files[0]["name"]).stat().st_mtime
    except OSError:
        return True
    return (time.time() - latest_mtime) >= BACKUP_INTERVAL_HOURS * 3600
