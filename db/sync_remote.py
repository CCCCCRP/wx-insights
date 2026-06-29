"""将本地 PostgreSQL 同步到阿里云 wxspirder 专用库（全量 pg_dump 或增量 UPSERT）。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from worker.config import db_sync_configured
from worker.db.sync_common import (
    SyncSettings,
    ensure_remote_postgres,
    load_sync_settings,
    run_scp,
    run_ssh,
)
from worker.db.sync_incremental import (
    load_sync_state,
    mark_sync_baseline,
    preview_incremental,
    resolve_sync_mode,
    save_sync_state,
    sync_database_incremental,
    since_param,
)

logger = logging.getLogger(__name__)

_COMPOSE_SRC = WORKER_ROOT / "deploy" / "server-pg-docker-compose.yml"
_REMOTE_DUMP = "/tmp/wxspirder-sync.dump"


@dataclass(frozen=True)
class PgConn:
    host: str
    port: int
    user: str
    password: str
    dbname: str


def parse_database_url(url: str) -> PgConn:
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"不支持的 DATABASE_URL scheme: {parsed.scheme}")
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        raise ValueError("DATABASE_URL 缺少数据库名")
    return PgConn(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "",
        password=parsed.password or "",
        dbname=dbname,
    )


def _pg_dump_via_docker(container: str, local: PgConn, dump_path: Path) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("未找到 pg_dump 且 docker 不可用")
    proc = subprocess.run(
        [
            docker, "exec", container,
            "pg_dump",
            "-U", local.user,
            "-d", local.dbname,
            "-Fc",
            "--no-owner",
            "--no-acl",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
        raise RuntimeError(f"docker pg_dump 失败: {detail or proc.returncode}")
    dump_path.write_bytes(proc.stdout)


def _pg_dump(local: PgConn, dump_path: Path, *, docker_container: str = "wxspirder-pg") -> None:
    pg_dump = shutil.which("pg_dump")
    if pg_dump:
        env = os.environ.copy()
        if local.password:
            env["PGPASSWORD"] = local.password
        cmd = [
            pg_dump,
            "-h", local.host,
            "-p", str(local.port),
            "-U", local.user,
            "-d", local.dbname,
            "-Fc",
            "--no-owner",
            "--no-acl",
            "-f", dump_path.as_posix(),
        ]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"pg_dump 失败: {detail or proc.returncode}")
        return

    if local.host in ("localhost", "127.0.0.1", "::1"):
        logger.info("本机无 pg_dump，改用 docker exec %s", docker_container)
        _pg_dump_via_docker(docker_container, local, dump_path)
        return

    raise RuntimeError(
        "未找到 pg_dump。请安装 PostgreSQL 客户端（brew install libpq），"
        "或确保本地库在 Docker 容器 wxspirder-pg 中"
    )


def _restore_on_remote(settings: SyncSettings, remote_dump: str) -> None:
    container = settings.remote_container
    db = settings.remote_db
    user = settings.remote_user
    inner_dump = "/tmp/wxspirder-restore.dump"

    script = (
        f"docker cp {remote_dump} {container}:{inner_dump} && "
        f"docker exec {container} pg_restore "
        f"-U {user} -d {db} --clean --if-exists --no-owner --no-acl {inner_dump} ; "
        f"docker exec {container} rm -f {inner_dump} ; "
        f"rm -f {remote_dump}"
    )
    proc = run_ssh(settings, script)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        # pg_restore 在 --clean 时可能因对象不存在返回非零，但数据通常已写入
        if "pg_restore: error" in detail.lower():
            raise RuntimeError(f"pg_restore 失败: {detail or proc.returncode}")
        logger.warning("pg_restore 返回 %d（可能为无害警告）: %s", proc.returncode, detail[:500])


def sync_database_to_remote(
    *,
    dry_run: bool = False,
    mode: str | None = None,
    mark_baseline: bool = False,
) -> None:
    """本地 DATABASE_URL → 阿里云 wxspirder-pg 容器。

    mode: auto（默认）| full | incremental
    - auto：有同步水位 → 增量，否则全量
    - mark_baseline：只标记当前为增量基线，不上传
    """
    if mark_baseline:
        if dry_run:
            logger.info("[dry-run] 跳过 mark-baseline")
            return
        mark_sync_baseline()
        return

    if not db_sync_configured():
        logger.info("远程 DB 同步未配置（需 DATABASE_URL + BLOG_SSH_HOST），跳过")
        return

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL 未配置")

    settings = load_sync_settings()
    local = parse_database_url(database_url)
    resolved = resolve_sync_mode(mode or DB_SYNC_MODE)

    logger.info(
        "DB 同步 [%s]: %s@%s:%d/%s → %s:%s",
        resolved,
        local.user,
        local.host,
        local.port,
        local.dbname,
        settings.ssh_host,
        settings.remote_dir,
    )

    if resolved == "incremental":
        state = load_sync_state()
        since = since_param(state)
        if dry_run:
            counts = preview_incremental(since)
            logger.info(
                "[dry-run] 增量 since=%s 变更: %s",
                since.isoformat(),
                ", ".join(f"{k}={v}" for k, v in counts.items() if v) or "无",
            )
            return
        sync_database_incremental(settings, since=since)
        return

    if dry_run:
        logger.info("[dry-run] 跳过全量 pg_dump / scp / pg_restore")
        return

    _sync_database_full(settings, local)


def _sync_database_full(settings: SyncSettings, local: PgConn) -> None:
    ensure_remote_postgres(settings)

    with tempfile.TemporaryDirectory(prefix="wxspirder-dump-") as tmp:
        dump_path = Path(tmp) / "wxspirder.dump"
        logger.info("全量导出本地数据库…")
        _pg_dump(local, dump_path)
        size_mb = dump_path.stat().st_size / (1024 * 1024)
        logger.info("dump 大小 %.1f MB", size_mb)

        logger.info("上传到服务器 %s …", _REMOTE_DUMP)
        run_scp(settings, dump_path, _REMOTE_DUMP)

    logger.info("在远程恢复数据库…")
    _restore_on_remote(settings, _REMOTE_DUMP)
    save_sync_state(mode="full", rows={})
    logger.info("全量 DB 同步完成")
