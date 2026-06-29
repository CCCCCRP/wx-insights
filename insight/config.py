"""加载 insight.yaml 与环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal

import yaml

from worker.config import CONFIG_DIR

INSIGHT_CONFIG_FILE = CONFIG_DIR / "insight.yaml"

EMBEDDING_DIMENSIONS = 1024
LLMBackend = Literal["local", "cloud"]


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip().strip('"').strip("'")


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _resolve_local_llm(llm_raw: Dict[str, Any], ollama_base: str) -> tuple[str, str]:
    local = llm_raw.get("local") or {}
    base_url = (
        _env("OLLAMA_LLM_BASE_URL")
        or str(local.get("base_url") or "")
        or f"{ollama_base.rstrip('/')}/v1"
    )
    api_key = _env("OLLAMA_API_KEY") or str(local.get("api_key") or "ollama")
    return _normalize_base_url(base_url), api_key


def _resolve_cloud_llm(llm_raw: Dict[str, Any]) -> tuple[str, str]:
    cloud = llm_raw.get("cloud") or {}
    api_key = (
        _env("DEEPSEEK_API_KEY")
        or _env("OPENAI_API_KEY")
        or _env("LLM_API_KEY")
    )
    base_url = (
        _env("OPENAI_BASE_URL")
        or _env("LLM_BASE_URL")
        or str(cloud.get("base_url") or "")
        or "https://api.deepseek.com"
    )
    return _normalize_base_url(base_url), api_key


def _phase_backend(phase_raw: Dict[str, Any], default: LLMBackend) -> LLMBackend:
    raw = str(phase_raw.get("backend") or default).strip().lower()
    return "local" if raw == "local" else "cloud"


def _env_bool(key: str) -> bool | None:
    raw = _env(key)
    if not raw:
        return None
    return raw.lower() in ("1", "true", "yes", "on")


def _resolve_bool(env_key: str, yaml_value: object, *, default: bool = False) -> bool:
    from_env = _env_bool(env_key)
    if from_env is not None:
        return from_env
    if yaml_value is None:
        return default
    return bool(yaml_value)


def _phase_no_think(phase_raw: Dict[str, Any], model: str) -> bool | None:
    if "no_think" not in phase_raw:
        return None
    return bool(phase_raw.get("no_think"))


@dataclass
class InsightSettings:
    reader_focus: List[str] = field(default_factory=list)
    # 兼容旧字段（= cloud）
    llm_base_url: str = ""
    llm_api_key: str = ""
    local_llm_base_url: str = ""
    local_llm_api_key: str = "ollama"
    cloud_llm_base_url: str = ""
    cloud_llm_api_key: str = ""
    phase_a_model: str = "qwen3:14b"
    phase_a_backend: LLMBackend = "local"
    phase_a_no_think: bool | None = None
    phase_a_max_tokens: int = 2048
    phase_a_max_concurrency: int = 2
    phase_a_content_truncate: int = 4000
    phase_a_retry: int = 2
    phase_b_model: str = "qwen3:14b"
    phase_b_backend: LLMBackend = "local"
    phase_b_no_think: bool | None = None
    phase_b_distance_threshold: float = 0.35
    phase_b_theme_min: int = 8
    phase_b_theme_max: int = 15
    phase_b_max_tokens: int = 16384
    phase_c_model: str = "deepseek-v4-flash"
    phase_c_backend: LLMBackend = "cloud"
    phase_c_max_input_tokens: int = 128000
    phase_c_max_tokens: int = 16384
    llm_max_tokens_ceiling: int = 65536
    phase_c_context_themes_limit: int = 15
    phase_c_rag_per_theme_limit: int = 4
    phase_c_rag_per_article_limit: int = 2
    phase_c_rag_total_limit: int = 30
    phase_c_rag_excerpt_chars: int = 400
    phase_c_rag_min_similarity: float = 0.58
    phase_c_rag_content_min_similarity: float = 0.50
    phase_c_rag_embedding_mode: str = "hybrid"
    phase_c_rag_tag_filter: bool = True
    embedding_backend: str = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = EMBEDDING_DIMENSIONS
    ollama_base_url: str = "http://localhost:11434"
    embedding_content_truncate: int = 6000
    embedding_summary_truncate: int = 2000
    embedding_batch_size: int = 32
    rolling_similarity_threshold: float = 0.72
    rolling_archive_days: int = 180
    profile_model: str = "qwen3:14b"
    profile_backend: LLMBackend = "local"
    profile_no_think: bool | None = None
    profile_max_tokens: int = 4096
    profile_min_summaries: int = 10
    profile_bootstrap_min_titles: int = 15
    profile_recalibrate_days: int = 90
    report_email_enabled: bool = True
    blog_enabled: bool = False
    blog_base_url: str = ""
    blog_remote_dir: str = "/var/www/insights"
    blog_ssh_host: str = ""
    blog_ssh_user: str = "root"
    blog_ssh_port: int = 0
    blog_ssh_identity: str = ""
    blog_ssh_password: str = ""
    blog_index_title: str = "洞见周报归档"
    blog_publish_on_generate: bool = True
    insights_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "insights")
    summary_cache_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "insights" / "cache" / "summaries")


def load_insight_settings() -> InsightSettings:
    raw: Dict[str, Any] = {}
    if INSIGHT_CONFIG_FILE.exists():
        raw = yaml.safe_load(INSIGHT_CONFIG_FILE.read_text(encoding="utf-8")) or {}

    llm_raw = raw.get("llm") or {}
    phase_a = raw.get("phase_a") or {}
    phase_b = raw.get("phase_b") or {}
    phase_c = raw.get("phase_c") or {}
    embedding = raw.get("embedding") or {}
    rolling = raw.get("rolling_themes") or {}
    profile = raw.get("profile") or {}
    report = raw.get("report") or {}
    blog = raw.get("blog") or {}

    ollama_base = str(
        embedding.get("ollama_base_url") or _env("OLLAMA_BASE_URL") or "http://localhost:11434"
    ).rstrip("/")
    local_base, local_key = _resolve_local_llm(llm_raw, ollama_base)
    cloud_base, cloud_key = _resolve_cloud_llm(llm_raw)

    email_env = _env("INSIGHT_REPORT_EMAIL", "").lower()
    if email_env in ("0", "false", "no", "off"):
        report_email_enabled = False
    elif email_env in ("1", "true", "yes", "on"):
        report_email_enabled = True
    else:
        report_email_enabled = bool(report.get("email_on_generate", True))

    phase_a_model = str(phase_a.get("model") or "qwen3:14b")
    phase_b_model = str(phase_b.get("model") or phase_a_model)
    profile_model = str(profile.get("model") or phase_a_model)

    base = Path(__file__).resolve().parent.parent / "data" / "insights"
    return InsightSettings(
        reader_focus=list(raw.get("reader_focus") or []),
        llm_base_url=cloud_base,
        llm_api_key=cloud_key,
        local_llm_base_url=local_base,
        local_llm_api_key=local_key,
        cloud_llm_base_url=cloud_base,
        cloud_llm_api_key=cloud_key,
        phase_a_model=phase_a_model,
        phase_a_backend=_phase_backend(phase_a, "local"),
        phase_a_no_think=_phase_no_think(phase_a, phase_a_model),
        phase_a_max_tokens=int(phase_a.get("max_tokens") or 2048),
        phase_a_max_concurrency=int(phase_a.get("max_concurrency") or 2),
        phase_a_content_truncate=int(phase_a.get("content_truncate_chars") or 4000),
        phase_a_retry=int(phase_a.get("retry") or 2),
        phase_b_model=phase_b_model,
        phase_b_backend=_phase_backend(phase_b, "local"),
        phase_b_no_think=_phase_no_think(phase_b, phase_b_model),
        phase_b_distance_threshold=float(phase_b.get("cluster_distance_threshold") or 0.35),
        phase_b_theme_min=int(phase_b.get("target_theme_count_min") or 8),
        phase_b_theme_max=int(phase_b.get("target_theme_count_max") or 15),
        phase_b_max_tokens=int(phase_b.get("max_tokens") or 16384),
        phase_c_model=str(phase_c.get("model") or "deepseek-v4-flash"),
        phase_c_backend=_phase_backend(phase_c, "cloud"),
        phase_c_max_input_tokens=int(phase_c.get("max_input_tokens") or 128000),
        phase_c_max_tokens=int(phase_c.get("max_tokens") or 16384),
        llm_max_tokens_ceiling=int(raw.get("llm_max_tokens_ceiling") or 65536),
        phase_c_context_themes_limit=int(phase_c.get("context_themes_limit") or 10),
        phase_c_rag_per_theme_limit=int(phase_c.get("rag_per_theme_limit") or 4),
        phase_c_rag_per_article_limit=int(phase_c.get("rag_per_article_limit") or 2),
        phase_c_rag_total_limit=int(phase_c.get("rag_total_limit") or 30),
        phase_c_rag_excerpt_chars=int(phase_c.get("rag_excerpt_chars") or 300),
        phase_c_rag_min_similarity=float(phase_c.get("rag_min_similarity") or 0.58),
        phase_c_rag_content_min_similarity=float(
            phase_c.get("rag_content_min_similarity") or 0.50
        ),
        phase_c_rag_embedding_mode=str(phase_c.get("rag_embedding_mode") or "hybrid"),
        phase_c_rag_tag_filter=bool(phase_c.get("rag_tag_filter", True)),
        embedding_backend=str(embedding.get("backend") or _env("EMBEDDING_BACKEND") or "ollama").lower(),
        embedding_model=str(embedding.get("model") or _env("EMBEDDING_MODEL") or "bge-m3"),
        embedding_dimensions=int(embedding.get("dimensions") or EMBEDDING_DIMENSIONS),
        ollama_base_url=ollama_base,
        embedding_content_truncate=int(embedding.get("content_truncate_chars") or 6000),
        embedding_summary_truncate=int(embedding.get("summary_truncate_chars") or 2000),
        embedding_batch_size=int(embedding.get("batch_size") or 32),
        rolling_similarity_threshold=float(rolling.get("similarity_threshold") or 0.72),
        rolling_archive_days=int(rolling.get("archive_after_days") or 180),
        profile_model=profile_model,
        profile_backend=_phase_backend(profile, "local"),
        profile_no_think=_phase_no_think(profile, profile_model),
        profile_max_tokens=int(profile.get("max_tokens") or 4096),
        profile_min_summaries=int(profile.get("min_summaries") or 10),
        profile_bootstrap_min_titles=int(profile.get("bootstrap_min_titles") or 15),
        profile_recalibrate_days=int(profile.get("recalibrate_days") or 90),
        report_email_enabled=report_email_enabled,
        blog_enabled=_resolve_bool("BLOG_ENABLED", blog.get("enabled"), default=False),
        blog_base_url=str(blog.get("base_url") or _env("BLOG_BASE_URL") or "").rstrip("/"),
        blog_remote_dir=str(blog.get("remote_dir") or _env("BLOG_REMOTE_DIR") or "/var/www/insights"),
        blog_ssh_host=str(blog.get("ssh_host") or _env("BLOG_SSH_HOST") or ""),
        blog_ssh_user=str(blog.get("ssh_user") or _env("BLOG_SSH_USER") or "root"),
        blog_ssh_port=int(blog.get("ssh_port") or _env("BLOG_SSH_PORT") or 0),
        blog_ssh_identity=str(blog.get("ssh_identity") or _env("BLOG_SSH_IDENTITY") or ""),
        blog_ssh_password=str(blog.get("ssh_password") or _env("BLOG_SSH_PASSWORD") or ""),
        blog_index_title=str(blog.get("index_title") or "洞见周报归档"),
        blog_publish_on_generate=_resolve_bool(
            "BLOG_PUBLISH_ON_GENERATE",
            blog.get("publish_on_generate"),
            default=bool(blog.get("enabled", False)),
        ),
        insights_dir=base,
        summary_cache_dir=base / "cache" / "summaries",
    )
