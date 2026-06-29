"""洞见报告 → HTML 渲染器。

从 PhaseCReportOutput + summaries 直接生成 HTML，不依赖 Markdown 解析。
"""
from __future__ import annotations

import html as _html
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from worker.insight.models import (
    ArticleSummaryRecord,
    CitedBullet,
    HistoryComparisonBullet,
    PhaseCReportOutput,
)
from worker.insight.report_builder import (
    AccountStats,
    build_account_stats,
    cite_link,
)
from worker.insight.retriever import dates_label_for_publish, lookback_section_title

logger = logging.getLogger(__name__)

# ── CSS ────────────────────────────────────────────────────────────────────────

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:          #f6f8fa;
  --surface:     #ffffff;
  --border:      #d0d7de;
  --text:        #1f2328;
  --text-sub:    #636c76;
  --accent:      #0969da;
  --accent-bg:   #ddf4ff;
  --tag-bg:      #eef2ff;
  --tag-color:   #3730a3;
  --section1-bg: #fff8e1;
  --section1-bd: #f59e0b;
  --section2-bg: #f0f9ff;
  --section2-bd: #0284c7;
  --section3-bg: #f0fdf4;
  --section3-bd: #16a34a;
  --cite-color:  #0969da;
  --radius:      10px;
  --shadow:      0 1px 4px rgba(0,0,0,.08);
}

body {
  font-family: 'Noto Sans SC', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 15px;
  line-height: 1.75;
  padding: 24px 16px 60px;
}

.container { max-width: 860px; margin: 0 auto; }

/* ── header ── */
.report-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 28px 32px 24px;
  margin-bottom: 28px;
  box-shadow: var(--shadow);
}
.report-header h1 {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
}
.report-header .subtitle {
  color: var(--text-sub);
  font-size: 13px;
  margin-top: 4px;
}

/* ── sources block ── */
.sources-block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 28px;
  box-shadow: var(--shadow);
}
.sources-block h2 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-sub);
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: 14px;
}
.account-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 6px 12px;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}
.account-row:first-of-type { border-top: none; }
.account-name {
  font-weight: 600;
  font-size: 14px;
  min-width: 100px;
  padding-top: 2px;
}
.account-articles { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; }
.art-chip {
  font-size: 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 1px 10px;
  color: var(--text);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  max-width: 240px;
  text-overflow: ellipsis;
  display: inline-block;
  transition: background .15s;
}
.art-chip:hover { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }

/* ── section title ── */
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-sub);
  text-transform: uppercase;
  letter-spacing: .06em;
  margin-bottom: 20px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

/* ── theme card ── */
.theme-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin-bottom: 20px;
  overflow: hidden;
}
.theme-header {
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border);
  background: #fafbfc;
}
.theme-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
}
.theme-tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }
.tag {
  font-size: 11px;
  background: var(--tag-bg);
  color: var(--tag-color);
  border-radius: 4px;
  padding: 1px 7px;
  font-weight: 500;
}

.theme-body { padding: 0; }

/* ── section within theme ── */
.theme-section {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
}
.theme-section:last-child { border-bottom: none; }

.section-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  margin-bottom: 10px;
}
.section-label .num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px; height: 20px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

/* section 1: brief */
.theme-section.brief { background: var(--section1-bg); border-left: 3px solid var(--section1-bd); }
.theme-section.brief .section-label { color: #92400e; }
.theme-section.brief .num { background: var(--section1-bd); color: #fff; }
.brief-text { font-size: 15px; font-weight: 500; color: var(--text); line-height: 1.65; }

/* section 2: details */
.theme-section.details { background: var(--section2-bg); border-left: 3px solid var(--section2-bd); }
.theme-section.details .section-label { color: #075985; }
.theme-section.details .num { background: var(--section2-bd); color: #fff; }

/* section 3: history */
.theme-section.history { background: var(--section3-bg); border-left: 3px solid var(--section3-bd); }
.theme-section.history .section-label { color: #14532d; }
.theme-section.history .num { background: var(--section3-bd); color: #fff; }

/* section 4: insights */
.theme-section.insights {
  background: linear-gradient(135deg, #fdf4ff 0%, #f5f0ff 100%);
  border-left: 3px solid #9333ea;
}
.theme-section.insights .section-label { color: #6b21a8; }
.theme-section.insights .num { background: #9333ea; color: #fff; }
.insight-list { list-style: none; display: flex; flex-direction: column; gap: 10px; }
.insight-list li {
  font-size: 14px;
  line-height: 1.75;
  padding: 8px 12px;
  background: rgba(147, 51, 234, 0.06);
  border-radius: 6px;
  color: var(--text);
  border-left: 2px solid rgba(147, 51, 234, 0.3);
}

/* ── bullet list ── */
.bullet-list { list-style: none; display: flex; flex-direction: column; gap: 8px; }
.bullet-list li {
  font-size: 14px;
  line-height: 1.7;
  padding-left: 14px;
  position: relative;
  color: var(--text);
}
.bullet-list li::before {
  content: '▸';
  position: absolute;
  left: 0;
  top: 0;
  color: var(--text-sub);
  font-size: 10px;
  line-height: 2.1;
}
.cite-link {
  display: inline-block;
  font-size: 11px;
  color: var(--cite-color);
  background: var(--accent-bg);
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 4px;
  text-decoration: none;
  vertical-align: middle;
  line-height: 1.6;
  transition: opacity .15s;
  white-space: nowrap;
}
.cite-link:hover { opacity: .75; text-decoration: underline; }
.cite-no-link {
  display: inline-block;
  font-size: 11px;
  color: var(--text-sub);
  background: var(--bg);
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 4px;
  vertical-align: middle;
  line-height: 1.6;
  white-space: nowrap;
  overflow: hidden;
  max-width: 140px;
  text-overflow: ellipsis;
}

/* ── follow-ups ── */
.follow-ups {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  box-shadow: var(--shadow);
  margin-top: 8px;
}
.follow-ups h2 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-sub);
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: 12px;
}
.follow-ups ol { padding-left: 20px; }
.follow-ups li { font-size: 14px; margin-bottom: 6px; color: var(--text); }

/* ── theme filter nav（无 JS 时不隐藏任何卡片）── */
.themes-section { margin-bottom: 8px; }
.theme-nav-wrap {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--bg);
  padding: 0 0 14px;
  margin-bottom: 4px;
}
.theme-nav-hint {
  display: none;
  font-size: 12px;
  color: var(--text-sub);
  margin-bottom: 10px;
}
body.has-theme-filter .theme-nav-hint { display: block; }
.theme-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.theme-pill {
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 20px;
  padding: 6px 14px;
  color: var(--text);
  transition: background .15s, border-color .15s, color .15s;
  max-width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.theme-pill:hover {
  background: var(--accent-bg);
  border-color: var(--accent);
  color: var(--accent);
}
.theme-pill.is-active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
body.has-theme-filter .theme-card.is-filtered-out { display: none; }

@media (max-width: 600px) {
  body { padding: 12px 10px 40px; font-size: 14px; }
  .theme-header, .theme-section { padding: 14px 16px; }
  .report-header, .sources-block, .follow-ups { padding: 16px 18px; }
}
"""

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
{body}
</div>
<script>{script}</script>
</body>
</html>
"""

_THEME_FILTER_JS = """
(function () {
  var nav = document.querySelector('.theme-nav');
  if (!nav) return;
  document.body.classList.add('has-theme-filter');

  var cards = document.querySelectorAll('.theme-card');
  var pills = nav.querySelectorAll('.theme-pill');

  function setFilter(idx) {
    pills.forEach(function (p) {
      p.classList.toggle('is-active', p.getAttribute('data-filter') === idx);
      p.setAttribute('aria-pressed', p.getAttribute('data-filter') === idx ? 'true' : 'false');
    });
    cards.forEach(function (c) {
      var match = idx === 'all' || c.getAttribute('data-theme-index') === idx;
      c.classList.toggle('is-filtered-out', !match);
    });
  }

  nav.addEventListener('click', function (e) {
    var pill = e.target.closest('.theme-pill');
    if (!pill) return;
    var idx = pill.getAttribute('data-filter');
    setFilter(idx);
    if (idx !== 'all') {
      var card = document.getElementById('theme-' + idx);
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  var hash = (location.hash || '').match(/^#theme-(\\d+)$/);
  if (hash) {
    setFilter(hash[1]);
  }
})();
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _e(text: str) -> str:
    """HTML-escape."""
    return _html.escape(str(text or ""), quote=True)


def _short(title: str, n: int = 20) -> str:
    t = (title or "").strip().replace("\n", " ")
    return (t[: n - 1] + "…") if len(t) > n else t


def _cite_badge(aid: str, index: Dict[str, ArticleSummaryRecord]) -> str:
    rec = index.get(aid)
    title, link, _ = cite_link(aid, index)
    pub = dates_label_for_publish(rec.publish_time) if rec else "?"
    label = _e(_short(title, 16))
    if link:
        return f'<a class="cite-link" href="{_e(link)}" target="_blank" title="{_e(title)}">↗ {pub} · {label}</a>'
    return f'<span class="cite-no-link">{pub} · {label}</span>'


def _history_bullets_html(
    bullets: List[HistoryComparisonBullet],
    week_index: Dict[str, ArticleSummaryRecord],
    history_index: Dict[str, ArticleSummaryRecord],
) -> str:
    items = []
    for b in bullets:
        if not (b.past_aid or "").strip() or b.past_aid not in history_index:
            continue
        past_rec = history_index.get(b.past_aid)
        now_rec = week_index.get(b.aid)
        past_dates = dates_label_for_publish(past_rec.publish_time) if past_rec else "?"
        now_dates = dates_label_for_publish(now_rec.publish_time) if now_rec else "?"
        past_text = _e(b.past_part.rstrip("。"))
        now_text = _e(b.this_week_part.rstrip("。"))
        past_badge = _cite_badge(b.past_aid, history_index)
        now_badge = _cite_badge(b.aid, week_index)
        items.append(
            f"<li>过去（{past_dates}）：{past_text}。{past_badge}；"
            f"本周（{now_dates}）：{now_text}。{now_badge}</li>"
        )
    if not items:
        return '<p style="font-size:13px;color:var(--text-sub)">本主题本周首次出现，暂无历史对比数据。</p>'
    return "<ul class='bullet-list'>" + "".join(items) + "</ul>"


def _bullets_html(
    bullets: List[CitedBullet],
    index: Dict[str, ArticleSummaryRecord],
) -> str:
    items = []
    for b in bullets:
        stmt = _e(b.statement.rstrip("。"))
        badge = _cite_badge(b.aid, index)
        items.append(f"<li>{stmt}。{badge}</li>")
    return "<ul class='bullet-list'>" + "".join(items) + "</ul>"


def _section(cls: str, num: str, label: str, inner: str) -> str:
    return (
        f'<div class="theme-section {cls}">'
        f'<div class="section-label">'
        f'<span class="num">{num}</span>'
        f'<span>{_e(label)}</span>'
        f'</div>'
        f'{inner}'
        f'</div>'
    )


def _theme_nav_html(themes) -> str:
    """主题 Pill 导航；无 JS 时仅作静态目录，不影响卡片展示。"""
    pills = [
        '<button type="button" class="theme-pill is-active" data-filter="all" '
        'aria-pressed="true">全部</button>'
    ]
    for i, theme in enumerate(themes):
        label = _e(_short(theme.theme, 14))
        tip = _e(theme.brief_summary)
        pills.append(
            f'<button type="button" class="theme-pill" data-filter="{i}" '
            f'aria-pressed="false" title="{tip}">{label}</button>'
        )
    return (
        '<div class="themes-section">'
        '<p class="section-title">分类洞见</p>'
        '<div class="theme-nav-wrap">'
        '<p class="theme-nav-hint">点击分类聚焦查看 · 默认展示全部 · 再次点「全部」恢复</p>'
        f'<nav class="theme-nav" aria-label="分类筛选">{"".join(pills)}</nav>'
        '</div>'
        '</div>'
    )


# ── public API ────────────────────────────────────────────────────────────────

def render_report_html(
    report: PhaseCReportOutput,
    summaries: List[ArticleSummaryRecord],
    *,
    week_id: str,
    history_index: Optional[Dict[str, ArticleSummaryRecord]] = None,
    output_path: Optional[Path] = None,
) -> str:
    """从结构化报告直接生成 HTML（不依赖 Markdown 解析）。"""
    index: Dict[str, ArticleSummaryRecord] = {s.aid: s for s in summaries}
    hist_index = history_index or {}
    account_stats: AccountStats = build_account_stats(summaries)

    parts: List[str] = []

    # ── header ────────────────────────────────────────────
    total = sum(len(v) for v in account_stats.values())
    parts.append(
        f'<div class="report-header">'
        f'<h1>洞见周报 · {_e(week_id)}</h1>'
        f'<p class="subtitle">本周收录 {total} 篇 · {len(account_stats)} 个公众号 · {len(report.themes)} 个分类</p>'
        f'</div>'
    )

    # ── sources ───────────────────────────────────────────
    parts.append('<div class="sources-block">')
    parts.append('<h2>本周更新来源</h2>')
    for nickname, arts in sorted(account_stats.items(), key=lambda x: -len(x[1])):
        chips = ""
        for a in arts:
            title = _short(a.title, 25)
            if a.link:
                chips += f'<a class="art-chip" href="{_e(a.link)}" target="_blank">{_e(title)}</a>'
            else:
                chips += f'<span class="art-chip">{_e(title)}</span>'
        count = len(arts)
        parts.append(
            f'<div class="account-row">'
            f'<span class="account-name">{_e(nickname)}'
            f' <span style="color:var(--text-sub);font-weight:400;font-size:12px">({count} 篇)</span>'
            f'</span>'
            f'<div class="account-articles">{chips}</div>'
            f'</div>'
        )
    parts.append('</div>')

    # ── themes ────────────────────────────────────────────
    parts.append(_theme_nav_html(report.themes))

    for i, theme in enumerate(report.themes):
        tags_html = "".join(f'<span class="tag">#{_e(t)}</span>' for t in theme.theme_tags[:6])

        # section 1: brief
        s1 = _section(
            "brief", "①", "一句话总结",
            f'<p class="brief-text">{_e(theme.brief_summary)}</p>',
        )

        # section 2: details
        s2 = _section(
            "details", "②", "详细概括",
            _bullets_html(theme.details, index),
        )

        hist_label = lookback_section_title(
            theme.lookback_days, theme.velocity_hint,
            rag_history_count=theme.rag_history_count,
        )

        # section 3: history comparison
        if theme.history_comparison:
            s3 = _section(
                "history", "③", hist_label,
                _history_bullets_html(theme.history_comparison, index, hist_index),
            )
        elif theme.rag_history_count <= 0:
            s3 = _section(
                "history", "③", "历史对比",
                '<p style="font-size:13px;color:var(--text-sub)">本主题本周首次出现，暂无历史对比数据。</p>',
            )
        else:
            s3 = _section(
                "history", "③", hist_label,
                '<p style="font-size:13px;color:var(--text-sub)">本主题为新出现话题，暂无历史对比数据。</p>',
            )

        # section 4: insights
        if theme.insights:
            items_html = "".join(f"<li>{_e(item)}</li>" for item in theme.insights)
            s4 = _section(
                "insights", "④", "启示与展望",
                f"<ul class='insight-list'>{items_html}</ul>",
            )
        else:
            s4 = ""

        parts.append(
            f'<div class="theme-card" id="theme-{i}" data-theme-index="{i}">'
            f'<div class="theme-header">'
            f'<div class="theme-name">{_e(theme.theme)}</div>'
            f'<div class="theme-tags">{tags_html}</div>'
            f'</div>'
            f'<div class="theme-body">{s1}{s2}{s3}{s4}</div>'
            f'</div>'
        )

    # ── follow-ups ────────────────────────────────────────
    if report.follow_ups:
        items = "".join(f"<li>{_e(item)}</li>" for item in report.follow_ups)
        parts.append(
            f'<div class="follow-ups">'
            f'<h2>值得跟进</h2>'
            f'<ol>{items}</ol>'
            f'</div>'
        )

    body = "\n".join(parts)
    html = _HTML_TEMPLATE.format(
        title=f"洞见周报 · {week_id}",
        css=_CSS,
        body=body,
        script=_THEME_FILTER_JS.strip(),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("HTML 已写入 %s", output_path)

    return html


def render_report_html_from_dir(report_dir: Path) -> Optional[str]:
    """从 report.meta.json 重新渲染 HTML（无需重跑 LLM）。"""
    meta_path = report_dir / "report.meta.json"
    if not meta_path.exists():
        logger.warning("report.meta.json 不存在: %s", meta_path)
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    structured = meta.get("structured_report")
    if not structured:
        logger.warning("meta 中无 structured_report: %s", meta_path)
        return None

    report = PhaseCReportOutput.model_validate(structured)
    week_id = meta.get("week_id", report_dir.name)
    raw_summaries = meta.get("summaries_index") or meta.get("summaries") or []
    summaries = [
        ArticleSummaryRecord(
            aid=s["aid"],
            fakeid=s.get("fakeid", ""),
            nickname=s.get("nickname", ""),
            title=s.get("title", ""),
            link=s.get("link", ""),
            summary=s.get("summary", ""),
            publish_time=int(s.get("publish_time") or 0),
        )
        for s in raw_summaries
    ]
    history_index = {
        s["aid"]: ArticleSummaryRecord(
            aid=s["aid"],
            fakeid=s.get("fakeid", ""),
            nickname=s.get("nickname", ""),
            title=s.get("title", ""),
            link=s.get("link", ""),
            summary=s.get("summary", ""),
            publish_time=int(s.get("publish_time") or 0),
        )
        for s in (meta.get("history_index") or [])
    }

    out_path = report_dir / "report.html"
    return render_report_html(
        report, summaries, week_id=week_id, history_index=history_index, output_path=out_path,
    )
