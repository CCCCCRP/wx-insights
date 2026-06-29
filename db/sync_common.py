"""DB 远程同步公共配置与 SSH 工具。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from worker.config import (
    DB_SYNC_REMOTE_CONTAINER,
    DB_SYNC_REMOTE_DB,
    DB_SYNC_REMOTE_DIR,
    DB_SYNC_REMOTE_PASSWORD,
    DB_SYNC_REMOTE_USER,
    DB_SYNC_SSH_HOST,
    DB_SYNC_SSH_IDENTITY,
    DB_SYNC_SSH_PASSWORD,
    DB_SYNC_SSH_PORT,
    DB_SYNC_SSH_USER,
    WORKER_ROOT,
)

logger = logging.getLogger(__name__)

COMPOSE_SRC = WORKER_ROOT / "deploy" / "server-pg-docker-compose.yml"


@dataclass(frozen=True)
class SyncSettings:
    ssh_host: str
    ssh_user: str
    ssh_port: int
    ssh_password: str
    ssh_identity: str
    remote_dir: str
    remote_container: str
    remote_user: str
    remote_password: str
    remote_db: str


def load_sync_settings() -> SyncSettings:
    if not DB_SYNC_SSH_HOST:
        raise RuntimeError("未配置 DB_SYNC_SSH_HOST 或 BLOG_SSH_HOST")
    return SyncSettings(
        ssh_host=DB_SYNC_SSH_HOST,
        ssh_user=DB_SYNC_SSH_USER or "root",
        ssh_port=DB_SYNC_SSH_PORT,
        ssh_password=DB_SYNC_SSH_PASSWORD,
        ssh_identity=DB_SYNC_SSH_IDENTITY,
        remote_dir=DB_SYNC_REMOTE_DIR,
        remote_container=DB_SYNC_REMOTE_CONTAINER,
        remote_user=DB_SYNC_REMOTE_USER,
        remote_password=DB_SYNC_REMOTE_PASSWORD,
        remote_db=DB_SYNC_REMOTE_DB,
    )


def _ssh_target(settings: SyncSettings) -> str:
    host = settings.ssh_host.strip()
    if "@" in host:
        return host
    return f"{settings.ssh_user}@{host}"


def _subprocess_env(password: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if password:
        env["SSHPASS"] = password
    return env


def _require_sshpass(password: str) -> str:
    if not password:
        return ""
    sshpass = shutil.which("sshpass")
    if not sshpass:
        raise RuntimeError(
            "已配置 SSH 密码，但未安装 sshpass（brew install hudochenkov/sshpass/sshpass）"
        )
    return sshpass


def _ssh_base_cmd(settings: SyncSettings) -> list[str]:
    sshpass = _require_sshpass(settings.ssh_password)
    ssh_opts = ["-o", "StrictHostKeyChecking=no"]
    if settings.ssh_port:
        ssh_opts.extend(["-p", str(settings.ssh_port)])
    if settings.ssh_identity:
        ssh_opts.extend(["-i", settings.ssh_identity])
    if sshpass:
        return [sshpass, "-e", "ssh", *ssh_opts]
    return ["ssh", *ssh_opts]


def _scp_base_cmd(settings: SyncSettings) -> list[str]:
    sshpass = _require_sshpass(settings.ssh_password)
    ssh_opts = ["-o", "StrictHostKeyChecking=no"]
    if settings.ssh_port:
        ssh_opts.extend(["-P", str(settings.ssh_port)])
    if settings.ssh_identity:
        ssh_opts.extend(["-i", settings.ssh_identity])
    if sshpass:
        return [sshpass, "-e", "scp", *ssh_opts]
    return ["scp", *ssh_opts]


def run_ssh(settings: SyncSettings, remote_cmd: str) -> subprocess.CompletedProcess[str]:
    cmd = _ssh_base_cmd(settings) + [_ssh_target(settings), remote_cmd]
    logger.debug("ssh: %s", remote_cmd)
    return subprocess.run(
        cmd,
        env=_subprocess_env(settings.ssh_password or None),
        capture_output=True,
        text=True,
    )


def run_scp(settings: SyncSettings, local_path: Path, remote_path: str) -> None:
    cmd = _scp_base_cmd(settings) + [local_path.as_posix(), f"{_ssh_target(settings)}:{remote_path}"]
    proc = subprocess.run(
        cmd,
        env=_subprocess_env(settings.ssh_password or None),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"scp 失败: {detail or proc.returncode}")


def ensure_remote_postgres(settings: SyncSettings) -> None:
    if not COMPOSE_SRC.is_file():
        raise FileNotFoundError(f"缺少部署文件: {COMPOSE_SRC}")

    remote_dir = settings.remote_dir.rstrip("/")
    proc = run_ssh(settings, f"mkdir -p {remote_dir}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"创建远程目录失败: {detail or proc.returncode}")

    logger.info("上传远程 PostgreSQL 部署文件（%s）", remote_dir)
    with tempfile.TemporaryDirectory(prefix="wxspirder-deploy-") as tmp:
        staging = Path(tmp)
        shutil.copy2(COMPOSE_SRC, staging / "docker-compose.yml")
        run_scp(settings, staging / "docker-compose.yml", f"{remote_dir}/docker-compose.yml")

    pg_password = settings.remote_password.replace("'", "'\\''")
    up_cmd = (
        f"cd {remote_dir} && "
        f"POSTGRES_PASSWORD='{pg_password}' docker compose up -d"
    )
    proc = run_ssh(settings, up_cmd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"启动远程 PostgreSQL 失败: {detail or proc.returncode}")

    for _ in range(30):
        proc = run_ssh(
            settings,
            f"docker exec {settings.remote_container} pg_isready -U {settings.remote_user} -d {settings.remote_db}",
        )
        if proc.returncode == 0:
            logger.info("远程 PostgreSQL 已就绪")
            return
        time.sleep(2)
    raise RuntimeError("远程 PostgreSQL 健康检查超时")
