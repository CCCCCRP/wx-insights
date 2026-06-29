"""统一日志配置：控制台 + 分模块滚动文件 + 可选总览日志。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Sequence

from worker.config import (
    LOG_AGGREGATE,
    LOG_CONSOLE,
    LOG_DIR,
    LOG_FILE,
    LOG_LEVEL,
    LOG_SPLIT,
)

_CONFIGURED = False

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 第三方库默认降噪
_QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "openai", "instructor")

# (文件名 stem, logger 前缀元组)
LOG_CATEGORIES: Sequence[tuple[str, Sequence[str]]] = (
    ("crawl", ("worker.crawl",)),
    ("insight", ("worker.insight",)),
    ("auth", ("worker.scan", "worker.auth")),
    ("schedule", ("worker.scheduler", "worker.mail")),
)


class LoggerPrefixFilter(logging.Filter):
    """仅放行 logger 名以给定前缀开头的记录。"""

    def __init__(self, prefixes: Sequence[str]) -> None:
        super().__init__()
        self._prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        return any(name.startswith(p) for p in self._prefixes)


class ExcludePrefixesFilter(logging.Filter):
    """排除已归入专项日志的前缀（用于 worker.log 仅存杂项）。"""

    def __init__(self, prefixes: Sequence[str]) -> None:
        super().__init__()
        self._prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        return not any(name.startswith(p) for p in self._prefixes)


def _all_category_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = []
    for _, ps in LOG_CATEGORIES:
        prefixes.extend(ps)
    return tuple(prefixes)


def _rotating_handler(path: Path, formatter: logging.Formatter, level: int) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def category_log_files() -> dict[str, Path]:
    """返回各专项日志路径 {crawl: ..., insight: ..., ...}。"""
    return {name: LOG_DIR / f"{name}.log" for name, _ in LOG_CATEGORIES}


def setup_logging(*, level: str | None = None, log_file: Path | None = None) -> Path:
    """初始化 root logger（幂等）。返回主日志文件路径。"""
    global _CONFIGURED
    primary = log_file or LOG_FILE
    if _CONFIGURED:
        return primary

    lvl_name = (level or LOG_LEVEL).upper()
    log_level = getattr(logging, lvl_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if LOG_CONSOLE:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console.setLevel(log_level)
        root.addHandler(console)

    category_prefixes = _all_category_prefixes()

    if LOG_SPLIT:
        for stem, prefixes in LOG_CATEGORIES:
            handler = _rotating_handler(LOG_DIR / f"{stem}.log", formatter, log_level)
            handler.addFilter(LoggerPrefixFilter(prefixes))
            root.addHandler(handler)

    if LOG_AGGREGATE or not LOG_SPLIT:
        aggregate = _rotating_handler(primary, formatter, log_level)
        root.addHandler(aggregate)
    elif LOG_SPLIT:
        misc = _rotating_handler(primary, formatter, log_level)
        misc.addFilter(ExcludePrefixesFilter(category_prefixes))
        root.addHandler(misc)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True
    log = logging.getLogger(__name__)
    if LOG_SPLIT:
        log.info(
            "日志已初始化 level=%s split=%s aggregate=%s dir=%s",
            lvl_name,
            LOG_SPLIT,
            LOG_AGGREGATE,
            LOG_DIR,
        )
        for stem, path in category_log_files().items():
            log.debug("  %s -> %s", stem, path)
    else:
        log.info("日志已初始化 level=%s file=%s", lvl_name, primary)
    return primary


def log_paths() -> dict[str, str]:
    """返回当前日志路径信息（供 status / test 使用）。"""
    paths: dict[str, str] = {
        "log_dir": str(LOG_DIR),
        "log_file": str(LOG_FILE),
        "log_level": LOG_LEVEL,
        "log_split": str(LOG_SPLIT),
        "log_aggregate": str(LOG_AGGREGATE),
        "console": str(LOG_CONSOLE),
    }
    if LOG_SPLIT:
        for stem, path in category_log_files().items():
            paths[f"log_{stem}"] = str(path)
    return paths
