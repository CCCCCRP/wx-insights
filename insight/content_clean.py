"""正文 boilerplate 清洗模块。

在 embed / Phase A 之前调用 clean_content(text, fakeid)。
- 全局规则：兜底，适用于所有账号（常见关注引导、推送提示等）
- 账号专属规则：来自 accounts.strip_head_pattern / strip_tail_markers，由 Profile LLM 自动检测写入
- 数据库读取：首次调用时加载并缓存，每 5 分钟重新加载一次（轻量级内存 TTL）
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 全局兜底规则（无需 DB，所有账号均适用）
# ──────────────────────────────────────────────────────────────────────────────

# 结尾截断标记：遇到任意一条即截断其后所有内容
GLOBAL_TAIL_MARKERS: List[str] = [
    "微信实行乱序推送",
    "长按下方图片关注",
    "点击下方名片关注",
    "点击下方卡片关注",
    "扫描下方二维码关注",
    "设为星标，不错过精彩内容",
    "设为星标不错过精彩内容",
]

# 开头行跳过前缀：若某行以这些字符串开头则跳过该行（不影响后续正文）
GLOBAL_HEAD_SKIP_PREFIXES: Tuple[str, ...] = (
    "👇",
    "⬇️",
    "↓",
    "☞关注",
    "►关注",
    "▶关注",
    "点击关注",
    "扫码关注",
    "【关注】",
)

# ──────────────────────────────────────────────────────────────────────────────
# DB 规则缓存（TTL = 300 秒）
# ──────────────────────────────────────────────────────────────────────────────

_CACHE_TTL = 300  # seconds
_cache: Optional[Dict[str, Dict]] = None
_cache_ts: float = 0.0


def _rules_cache() -> Dict[str, Dict]:
    """返回 {fakeid: {strip_head_pattern, strip_tail_markers}} 的内存缓存。"""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is None or (now - _cache_ts) > _CACHE_TTL:
        try:
            from worker.db import insight_repo
            _cache = insight_repo.fetch_strip_rules_for_all_accounts()
            _cache_ts = now
            logger.debug("已加载 %d 条账号 strip 规则", len(_cache))
        except Exception as exc:
            logger.warning("加载 strip 规则失败（跳过账号专属清洗）: %s", exc)
            _cache = {}
            _cache_ts = now
    return _cache


def invalidate_cache() -> None:
    """手动让缓存失效（profile 更新后调用）。"""
    global _cache
    _cache = None


# ──────────────────────────────────────────────────────────────────────────────
# 主清洗函数
# ──────────────────────────────────────────────────────────────────────────────

def clean_content(text: str, fakeid: str = "") -> str:
    """对正文应用头尾 boilerplate 清洗，返回清洗后文本。

    Args:
        text:   原始 plain_content（已去 HTML）
        fakeid: 公众号 fakeid，用于查找账号专属规则

    Returns:
        清洗后的文本；若无需清洗则返回原字符串（无 copy 开销）
    """
    if not text:
        return text

    # ── 1. 账号专属头部规则 ─────────────────────────────────────────────────
    head_pattern = ""
    tail_markers: List[str] = []
    if fakeid:
        rules = _rules_cache().get(fakeid, {})
        head_pattern = rules.get("strip_head_pattern", "")
        tail_markers = list(rules.get("strip_tail_markers") or [])

    # ── 2. 去除开头 boilerplate ────────────────────────────────────────────
    lines = text.split("\n")
    start_idx = 0

    # 2a. 账号专属：逐字匹配前缀行（连续匹配，允许多行头部模板）
    if head_pattern:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and stripped.startswith(head_pattern.strip()):
                start_idx = i + 1
                break

    # 2b. 全局：跳过开头若干行的关注引导（从 start_idx 起算）
    while start_idx < len(lines):
        ln = lines[start_idx].strip()
        if ln and ln.startswith(GLOBAL_HEAD_SKIP_PREFIXES):
            start_idx += 1
        else:
            break

    # ── 3. 确定尾部截断位置 ────────────────────────────────────────────────
    end_idx = len(lines)
    all_tail_markers = tail_markers + GLOBAL_TAIL_MARKERS

    for marker in all_tail_markers:
        for i in range(end_idx - 1, start_idx - 1, -1):
            if marker in lines[i]:
                end_idx = i
                break

    # ── 4. 重组并去除两端空行 ─────────────────────────────────────────────
    cleaned_lines = lines[start_idx:end_idx]
    # 去除头尾空行
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    if not cleaned_lines:
        return text  # 全部被清洗掉时回退原文

    result = "\n".join(cleaned_lines)
    return result


def clean_for_embed(text: str, fakeid: str = "", truncate: int = 0) -> str:
    """清洗后截断，供 embedder 调用。

    Args:
        text:     原始 plain_content
        fakeid:   公众号 fakeid
        truncate: >0 时截断到指定字符数（0 = 不截断）
    """
    cleaned = clean_content(text, fakeid)
    if truncate > 0:
        return cleaned[:truncate]
    return cleaned
