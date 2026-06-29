"""邮件收件人解析与归一化。"""
from __future__ import annotations


from worker.config import parse_email_list


def resolve_recipients(to: list[str] | None, fallback: list[str]) -> list[str]:
    """合并显式收件人与默认列表；显式传入时仅使用显式列表。"""
    if to is not None:
        cleaned = [addr for addr in to if addr]
        return cleaned
    return list(fallback)
