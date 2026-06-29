"""增量 DB 同步：按时间水位导出变更行，远程 UPSERT。"""
from __future__ import annotations

import json
import logging
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worker.config import DATA_DIR
from worker.db.connection import get_conn
from worker.db.sync_common import SyncSettings, ensure_remote_postgres, run_scp, run_ssh

logger = logging.getLogger(__name__)

_STATE_FILE = DATA_DIR / "db_sync_state.json"
_OVERLAP = timedelta(minutes=2)

# 同步顺序：先 accounts，再 articles / summaries（逻辑关联）
_TABLE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("accounts", "updated_at >= %s", "fakeid"),
    ("articles", "(updated_at >= %s OR crawled_at >= %s)", "id"),
    ("article_summaries", "generated_at >= %s", "aid"),
    ("themes", "updated_at >= %s", "theme_key"),
    ("crawl_runs", "crawled_at >= %s", "id"),
    ("insights", "generated_at >= %s", "week_id"),
    ("schema_migrations", "TRUE", "version"),
)


@dataclass(frozen=True)
class SyncState:
    last_sync_at: datetime
    last_mode: str
    rows: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        return {
            "last_sync_at": self.last_sync_at.astimezone(timezone.utc).isoformat(),
            "last_mode": self.last_mode,
            "rows": self.rows,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SyncState:
        raw = data["last_sync_at"]
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return cls(
            last_sync_at=datetime.fromisoformat(raw),
            last_mode=str(data.get("last_mode", "unknown")),
            rows={str(k): int(v) for k, v in (data.get("rows") or {}).items()},
        )


def load_sync_state() -> SyncState | None:
    if not _STATE_FILE.is_file():
        return None
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return SyncState.from_json(data)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("读取同步水位失败，将回退全量: %s", e)
        return None


def save_sync_state(*, mode: str, rows: dict[str, int]) -> SyncState:
    state = SyncState(
        last_sync_at=datetime.now(timezone.utc),
        last_mode=mode,
        rows=rows,
    )
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return state


def mark_sync_baseline() -> SyncState:
    """标记当前时刻为增量基线（已手动全量同步后使用）。"""
    state = save_sync_state(mode="baseline", rows={})
    logger.info("已标记增量基线: %s", state.last_sync_at.isoformat())
    return state


def resolve_sync_mode(mode: str) -> str:
    if mode in ("full", "incremental"):
        return mode
    return "incremental" if load_sync_state() else "full"


def since_param(state: SyncState | None) -> datetime:
    if state is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    since = state.last_sync_at.astimezone(timezone.utc) - _OVERLAP
    return since


def _where_params(where: str, since: datetime) -> tuple[Any, ...]:
    count = where.count("%s")
    if count == 0:
        return ()
    if count == 2:
        return (since, since)
    return (since,)


def _table_columns(table: str) -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            cols = [row[0] for row in cur.fetchall()]
    if not cols:
        raise RuntimeError(f"找不到表 {table} 的列定义")
    return cols


def _upsert_sql(table: str, pk: str, columns: list[str]) -> str:
    col_list = ", ".join(columns)
    conflict_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != pk)
    return (
        f"INSERT INTO {table} ({col_list}) "
        f"SELECT {col_list} FROM _stg_{table} "
        f"ON CONFLICT ({pk}) DO UPDATE SET {conflict_cols}"
    )


def _export_table(table: str, where: str, params: tuple[Any, ...], out: Path) -> int:
    sql = f"COPY (SELECT * FROM {table} WHERE {where}) TO STDOUT WITH (FORMAT binary)"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
            count = int(cur.fetchone()[0])
            if count == 0:
                out.write_bytes(b"")
                return 0
            with out.open("wb") as f:
                cur.copy_expert(sql, f)
    return count


def _count_since(table: str, where: str, since: datetime) -> int:
    params = _where_params(where, since)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
            return int(cur.fetchone()[0])


def preview_incremental(since: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, where, _pk in _TABLE_SPECS:
        counts[table] = _count_since(table, where, since)
    return counts


def _apply_remote_bundle(settings: SyncSettings, remote_bundle: str) -> None:
    container = settings.remote_container
    user = settings.remote_user
    db = settings.remote_db
    remote_dir = remote_bundle.rstrip("/")

    lines = ["set -e"]
    for table, _where, pk in _TABLE_SPECS:
        columns = _table_columns(table)
        upsert = _upsert_sql(table, pk, columns)
        lines.extend([
            f"if [ -s {remote_dir}/{table}.bin ]; then",
            f"  docker cp {remote_dir}/{table}.bin {container}:/tmp/{table}.bin",
            (
                f"  docker exec {container} psql -U {user} -d {db} -v ON_ERROR_STOP=1 -c "
                f"\"BEGIN; CREATE TEMP TABLE _stg_{table} (LIKE {table} INCLUDING ALL) ON COMMIT DROP; "
                f"COPY _stg_{table} FROM '/tmp/{table}.bin' WITH (FORMAT binary); "
                f"{upsert}; COMMIT;\""
            ),
            f"  docker exec {container} rm -f /tmp/{table}.bin",
            "fi",
        ])
    lines.append(f"rm -rf {remote_dir}")
    script = "\n".join(lines)
    remote_cmd = f"bash -s <<'WXEOF'\n{script}\nWXEOF"
    proc = run_ssh(settings, remote_cmd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"远程增量应用失败: {detail or proc.returncode}")


def sync_database_incremental(
    settings: SyncSettings,
    *,
    since: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    state = load_sync_state()
    since = since or since_param(state)
    counts = preview_incremental(since)
    total = sum(counts.values())

    logger.info(
        "增量同步 since=%s，变更行: %s（共 %d）",
        since.isoformat(),
        ", ".join(f"{k}={v}" for k, v in counts.items() if v),
        total,
    )
    if dry_run:
        logger.info("[dry-run] 跳过增量上传")
        return counts

    if total == 0:
        save_sync_state(mode="incremental", rows=counts)
        logger.info("无变更，跳过上传")
        return counts

    ensure_remote_postgres(settings)
    remote_bundle = "/tmp/wxspirder-incr"

    with tempfile.TemporaryDirectory(prefix="wxspirder-incr-") as tmp:
        staging = Path(tmp)
        manifest = {"since": since.isoformat(), "tables": counts}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        for table, where, _pk in _TABLE_SPECS:
            params = _where_params(where, since)
            _export_table(table, where, params, staging / f"{table}.bin")

        archive = staging / "bundle.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in staging.iterdir():
                tar.add(path, arcname=path.name)

        proc = run_ssh(settings, f"rm -rf {remote_bundle} && mkdir -p {remote_bundle}")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"创建远程目录失败: {detail or proc.returncode}")

        run_scp(settings, archive, f"{remote_bundle}.tar.gz")
        extract_cmd = (
            f"tar -xzf {remote_bundle}.tar.gz -C {remote_bundle} "
            f"&& rm -f {remote_bundle}.tar.gz"
        )
        proc = run_ssh(settings, extract_cmd)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"解压远程包失败: {detail or proc.returncode}")

        _apply_remote_bundle(settings, remote_bundle)

    save_sync_state(mode="incremental", rows=counts)
    logger.info("增量 DB 同步完成")
    return counts
