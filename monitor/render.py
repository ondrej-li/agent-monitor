"""Rendering: markdown, email HTML, dashboard and chat UI."""

from __future__ import annotations

import html
import json
import pathlib
import re
from datetime import datetime
from urllib.parse import quote

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

LIGHT = {
    "page_bg": "#f6f8fb",
    "surface": "#ffffff",
    "raise": "#ffffff",
    "soft": "#f1f5f9",
    "ink": "#0f172a",
    "muted": "#64748b",
    "line": "#e2e8f0",
    "body": "#475569",
    "body2": "#334155",
    "accent": "#1d4ed8",
    "warn": "#b45309",
    "danger": "#b91c1c",
    "ok": "#047857",
    "danger_bg": "#fff7f6",
    "danger_line": "#fde2e1",
    "ok_bg": "#ecfdf5",
    "accent_bg": "#eff6ff",
    "warn_bg": "#fffbeb",
    "warn_badge": "#fef3c7",
}

PAGE = {
    "page_bg": "var(--page-bg)",
    "surface": "var(--surface)",
    "raise": "var(--raise)",
    "soft": "var(--soft)",
    "ink": "var(--ink)",
    "muted": "var(--muted)",
    "line": "var(--line)",
    "body": "var(--body)",
    "body2": "var(--body2)",
    "accent": "var(--accent)",
    "warn": "var(--warn)",
    "danger": "var(--danger)",
    "ok": "var(--ok)",
    "danger_bg": "var(--danger-bg)",
    "danger_line": "var(--danger-line)",
    "ok_bg": "var(--ok-bg)",
    "accent_bg": "var(--accent-bg)",
    "warn_bg": "var(--warn-bg)",
    "warn_badge": "var(--warn-badge)",
}

LIGHT_VARS = """
--page-bg:#f6f8fb;--surface:#ffffff;--raise:#ffffff;--soft:#f1f5f9;
--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--body:#475569;--body2:#334155;
--accent:#1d4ed8;--accent-strong:#1d4ed8;--warn:#b45309;--warn-bg:#fffbeb;
--warn-badge:#fef3c7;--danger:#b91c1c;--danger-bg:#fff7f6;--danger-line:#fde2e1;
--ok:#047857;--ok-bg:#ecfdf5;--accent-bg:#eff6ff;--shadow:rgba(15,23,42,.06);
"""
DARK_VARS = """
--page-bg:#0b1020;--surface:#121a2e;--raise:#17203a;--soft:#182342;
--ink:#e5e9f2;--muted:#94a3b8;--line:#26355a;--body:#cbd5e1;--body2:#cbd5e1;
--accent:#93b4ff;--accent-strong:#2563eb;--warn:#fbbf24;--warn-bg:#2a2213;
--warn-badge:#3a2d12;--danger:#fca5a5;--danger-bg:#2a1416;--danger-line:#4a2224;
--ok:#34d399;--ok-bg:#10251d;--accent-bg:#16213c;--shadow:rgba(0,0,0,.35);
"""

PAGE_RULES = """
*{box-sizing:border-box}
body{margin:0;background:var(--page-bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55}
a{color:var(--accent);text-decoration:none}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;max-width:900px;margin:0 auto;padding:22px 20px 6px;flex-wrap:wrap}
.brand{font-weight:700;font-size:17px;letter-spacing:-.01em;color:var(--ink)}
nav{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
nav a{padding:6px 11px;border-radius:999px;font-size:13px;color:var(--muted);border:1px solid transparent}
nav a.active{background:var(--surface);border-color:var(--line);color:var(--ink);font-weight:600}
main{max-width:900px;margin:0 auto;padding:12px 20px 60px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px 24px;box-shadow:0 1px 2px var(--shadow)}
button{font:inherit;font-size:13px;font-weight:600;padding:7px 13px;border-radius:9px;border:1px solid var(--line);background:var(--soft);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent-strong);border-color:var(--accent-strong);color:#fff}
button.ghost{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:650}
.muted{color:var(--muted)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:650;background:var(--soft);color:var(--muted)}
.pill.ok{background:var(--ok-bg);color:var(--ok)}
.pill.err{background:var(--danger-bg);color:var(--danger)}
.pill.warn{background:var(--warn-bg);color:var(--warn)}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.actions{display:flex;gap:6px}
.empty{padding:40px 0;text-align:center;color:var(--muted)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;background:var(--soft);padding:1px 5px;border-radius:5px}
/* history */
.tl{display:flex;flex-direction:column}
.tl-item{display:flex;gap:14px;justify-content:space-between;padding:13px 0;border-top:1px solid var(--line)}
.tl-item:first-child{border-top:none}
.tl-main{flex:1;min-width:0}
.tl-title{font-weight:600;font-size:14px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.tl-meta{font-size:11px;color:var(--muted);margin-top:5px}
.tl-actions{display:flex;gap:6px;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end}
.linkbtn{font-size:12px;font-weight:600;padding:6px 10px;border-radius:9px;border:1px solid var(--line);color:var(--accent);white-space:nowrap;text-decoration:none}
.kind{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;padding:2px 7px;border-radius:999px;background:var(--soft);color:var(--muted);border:1px solid var(--line)}
.kind.risk{background:var(--warn-bg);color:var(--warn);border-color:transparent}
.kind.needs_you{background:var(--danger-bg);color:var(--danger);border-color:transparent}
/* chat */
.chat-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.chat-thread{font-weight:650;font-size:15px}
.chat-box{display:flex;flex-direction:column;gap:10px;max-height:60vh;overflow-y:auto;padding:4px 2px 12px}
.msg{max-width:86%;padding:11px 14px;border-radius:13px;font-size:14px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.user{align-self:flex-end;background:var(--accent-strong);color:#fff;border-bottom-right-radius:4px}
.msg.assistant{align-self:flex-start;background:var(--soft);color:var(--ink);border:1px solid var(--line);border-bottom-left-radius:4px}
.msg.pending{font-style:italic;opacity:.75}
.msg.error{background:var(--danger-bg);border-color:var(--danger-line);color:var(--danger);font-style:normal}
.chat-form{display:flex;gap:10px;align-items:flex-end;margin-top:12px}
.chat-form textarea{flex:1;min-height:66px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--page-bg);color:var(--ink);font:inherit;resize:vertical}
.chat-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chips a{padding:4px 10px;border:1px solid var(--line);border-radius:999px;font-size:12px;color:var(--muted)}
.chips a.active{background:var(--soft);color:var(--ink);font-weight:600}
/* 3rd-party class used by email body when forced light */
:root[data-theme="dark"] .force-light{background:#fff}
"""

PAGE_CSS = (
    ':root{color-scheme:light;' + LIGHT_VARS + '}\n'
    ':root[data-theme="dark"]{color-scheme:dark;' + DARK_VARS + '}\n'
    '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;' + DARK_VARS + '}}\n'
    + PAGE_RULES
)

THEME_BOOT = (
    "<script>try{var t=localStorage.getItem('theme');"
    "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>"
)

THEME_TOGGLE_JS = """
<script>
(function(){
  var root=document.documentElement;
  var btn=document.getElementById('theme-toggle');
  if(!btn) return;
  var modes=['auto','light','dark'];
  var labels={auto:'Auto',light:'Light',dark:'Dark'};
  function current(){try{var t=localStorage.getItem('theme');return modes.indexOf(t)>-1?t:'auto';}catch(e){return 'auto';}}
  function apply(m){if(m==='auto'){root.removeAttribute('data-theme');}else{root.setAttribute('data-theme',m);}btn.textContent=labels[m];}
  btn.addEventListener('click',function(){var next=modes[(modes.indexOf(current())+1)%3];try{localStorage.setItem('theme',next);}catch(e){}apply(next);});
  apply(current());
})();
</script>
"""


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(url: str) -> str:
    candidate = (url or "").strip()
    return candidate if re.match(r"^https?://", candidate, re.IGNORECASE) else ""


def item_key_from_url(url: str) -> str:
    match = re.match(
        r"^https?://github\.com/([^/]+)/([^/]+)/(?:pull|issues)/(\d+)", (url or "").strip()
    )
    return f"{match.group(1)}/{match.group(2)}#{match.group(3)}" if match else ""


def _when(meta: dict) -> datetime:
    value = meta.get("when")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _link(url: str, label: str, palette: dict, *, weight: str = "500") -> str:
    target = safe_url(url)
    if not target:
        return f'<span style="color:{palette["ink"]};font-weight:{weight}">{esc(label)}</span>'
    return (
        f'<a href="{esc(target)}" style="color:{palette["accent"]};font-weight:{weight};'
        f'text-decoration:none">{esc(label)}</a>'
    )


def _thread_link(thread: str, palette: dict, label: str = "discuss") -> str:
    if not thread:
        return ""
    return (
        f' <a href="/chat?thread={quote(thread, safe=":")}" style="color:{palette["muted"]};'
        f'font-size:12px;font-weight:500;text-decoration:none;white-space:nowrap">{esc(label)}</a>'
    )


def _item_thread(url: str) -> str:
    key = item_key_from_url(url)
    return f"item:{key}" if key else ""


def _badge(text: str, color: str, background: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 7px;border-radius:999px;'
        f'background:{background};color:{color};font-size:11px;font-weight:650;'
        f'letter-spacing:.02em;white-space:nowrap">{esc(text)}</span>'
    )


def _stat(value, label: str, palette: dict) -> str:
    return (
        '<td style="padding:0 14px 0 0;vertical-align:top;border-bottom:none">'
        f'<div style="font-size:20px;font-weight:650;color:{palette["ink"]};line-height:1.1">{esc(value)}</div>'
        f'<div style="font-size:11px;color:{palette["muted"]};text-transform:uppercase;'
        f'letter-spacing:.06em">{esc(label)}</div></td>'
    )


def _section_title(title: str, count: int | None, palette: dict, color: str | None = None) -> str:
    suffix = (
        f'<span style="color:{palette["muted"]};font-weight:500;font-size:13px"> · {count}</span>'
        if count is not None
        else ""
    )
    return (
        f'<h2 style="margin:26px 0 10px;font-size:13px;text-transform:uppercase;'
        f'letter-spacing:.08em;color:{color or palette["ink"]};font-weight:700">'
        f"{esc(title)}{suffix}</h2>"
    )


def _empty(text: str, palette: dict) -> str:
    return f'<div style="color:{palette["muted"]};font-size:13px;padding:2px 0 6px">{esc(text)}</div>'


def digest_body_html(digest: dict, meta: dict, *, palette: dict | None = None, interactive: bool = False) -> str:
    p = palette or LIGHT
    when = _when(meta)
    counts = meta.get("counts") or {}
    prefix = meta.get("prefix") or "Secretary"
    needs = digest.get("needs_you") or []
    projects = digest.get("projects") or []
    ideas = digest.get("ideas") or []
    risks = digest.get("risks") or []
    actions = digest.get("next_actions") or []

    parts: list[str] = []
    parts.append(
        f'<div style="font-family:{FONT};max-width:660px;margin:0 auto;color:{p["ink"]};'
        f'font-size:15px;line-height:1.55;text-align:left">'
    )
    parts.append(
        f'<div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;'
        f'color:{p["muted"]};font-weight:650">{esc(prefix)} · '
        f'{when.strftime("%a %d %b %Y · %H:%M")}</div>'
    )
    headline = digest.get("headline") or "Briefing"
    parts.append(
        f'<h1 style="margin:8px 0 14px;font-size:24px;line-height:1.25;'
        f'font-weight:700;letter-spacing:-.01em">{esc(headline)}</h1>'
    )
    if meta.get("degraded"):
        parts.append(
            '<div style="margin:0 0 14px">'
            + _badge("deterministic summary — model unavailable", p["warn"], p["warn_badge"])
            + "</div>"
        )
    parts.append('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 8px"><tr>')
    parts.append(_stat(counts.get("new", 0), "new", p))
    parts.append(_stat(counts.get("changed", 0), "changed", p))
    parts.append(_stat(counts.get("open", 0), "open", p))
    parts.append(_stat(counts.get("commits", 0), "commits", p))
    parts.append("</tr></table>")

    parts.append(_section_title("Needs you", len(needs), p, p["danger"]))
    if needs:
        rows = []
        for index, entry in enumerate(needs):
            border = f"border-top:1px solid {p['line']};" if index else ""
            why = (
                f'<div style="font-size:13px;color:{p["body"]};margin-top:2px">{esc(entry.get("why"))}</div>'
                if entry.get("why")
                else ""
            )
            discuss = ""
            if interactive:
                thread = (
                    f"alert:{entry['alert_id']}"
                    if entry.get("alert_id")
                    else _item_thread(entry.get("link", ""))
                )
                discuss = _thread_link(thread, p)
            rows.append(
                f'<div style="padding:9px 0;{border}">'
                f'{_link(entry.get("link", ""), entry.get("title", ""), p, weight="600")}'
                f"{discuss}{why}</div>"
            )
        parts.append(
            f'<div style="border:1px solid {p["danger_line"]};border-left:3px solid {p["danger"]};'
            f'background:{p["danger_bg"]};border-radius:10px;padding:6px 16px">{"".join(rows)}</div>'
        )
    else:
        parts.append(_empty("Nothing is blocked on you.", p))

    parts.append(_section_title("Projects", len(projects), p))
    if projects:
        for project in projects:
            bullets = "".join(
                f'<li style="margin:4px 0">{esc(bullet)}</li>' for bullet in project.get("bullets") or []
            )
            status = (
                f'<div style="font-size:13px;color:{p["body"]};margin-top:2px">'
                f'{esc(project.get("status_line"))}</div>'
                if project.get("status_line")
                else ""
            )
            list_html = (
                f'<ul style="margin:8px 0 0 18px;padding:0;font-size:14px;color:{p["body2"]}">{bullets}</ul>'
                if bullets
                else ""
            )
            parts.append(
                f'<div style="margin:0 0 16px;padding-left:12px;border-left:2px solid {p["line"]}">'
                f'<div style="font-weight:650;font-size:15px">{esc(project.get("name"))}</div>'
                f"{status}{list_html}</div>"
            )
    else:
        parts.append(_empty("No project activity in this window.", p))

    parts.append(_section_title("Ideas", len(ideas), p, p["accent"]))
    if ideas:
        for idea in ideas:
            effort = idea.get("effort") or "M"
            effort_color = {"S": p["ok"], "M": p["accent"], "L": p["warn"]}.get(effort, p["accent"])
            effort_bg = {"S": p["ok_bg"], "M": p["accent_bg"], "L": p["warn_bg"]}.get(effort, p["accent_bg"])
            area = (
                f'<span style="font-size:12px;color:{p["muted"]};font-family:{MONO}">'
                f'{esc(idea.get("area"))}</span>'
                if idea.get("area")
                else ""
            )
            why = (
                f'<div style="font-size:13px;color:{p["body"]};margin-top:4px">{esc(idea.get("why_now"))}</div>'
                if idea.get("why_now")
                else ""
            )
            discuss = ""
            if interactive:
                thread = (
                    f"idea:{idea['idea_id']}"
                    if idea.get("idea_id")
                    else _item_thread(idea.get("link", ""))
                )
                discuss = _thread_link(thread, p)
            parts.append(
                f'<div style="border:1px solid {p["line"]};border-radius:10px;padding:12px 14px;'
                f'margin:0 0 10px;background:{p["raise"]}">'
                f'<div style="margin-bottom:4px">{_badge(effort + " · effort", effort_color, effort_bg)} '
                f'<span style="margin-left:6px">{area}</span></div>'
                f'<div style="font-weight:600;font-size:14px">'
                f'{_link(idea.get("link", ""), idea.get("title", ""), p, weight="600")}{discuss}</div>'
                f"{why}</div>"
            )
    else:
        parts.append(_empty("No new ideas in this run.", p))

    if risks:
        parts.append(_section_title("Risks", len(risks), p, p["warn"]))
        items = "".join(f'<li style="margin:4px 0">{esc(risk)}</li>' for risk in risks)
        parts.append(
            f'<ul style="margin:0 0 0 18px;padding:0;font-size:14px;color:{p["body2"]}">{items}</ul>'
        )

    if actions:
        parts.append(_section_title("Next actions", len(actions), p))
        items = "".join(f'<li style="margin:5px 0">{esc(action)}</li>' for action in actions)
        parts.append(
            f'<ol style="margin:0 0 0 18px;padding:0;font-size:14px;color:{p["body2"]}">{items}</ol>'
        )

    model = meta.get("model") or meta.get("provider") or "unknown model"
    duration = meta.get("duration_s")
    duration_text = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "—"
    dashboard = safe_url(meta.get("dashboard_url", ""))
    footer_link = (
        f' · <a href="{esc(dashboard)}" style="color:{p["muted"]}">open dashboard</a>' if dashboard else ""
    )
    parts.append(
        f'<div style="margin-top:26px;padding-top:12px;border-top:1px solid {p["line"]};'
        f'font-size:11px;color:{p["muted"]};line-height:1.5">'
        f'agent-monitor · {esc(model)} · {esc(duration_text)} · '
        f'{esc(counts.get("scanned", 0))} items scanned{footer_link}</div>'
    )
    parts.append("</div>")
    return "".join(parts)


def email_subject(digest: dict, meta: dict) -> str:
    prefix = meta.get("prefix") or "Secretary"
    when = _when(meta)
    needs = len(digest.get("needs_you") or [])
    ideas = len(digest.get("ideas") or [])
    bits = [f"{needs} need you" if needs else "clear"]
    if ideas:
        bits.append(f"{ideas} idea{'s' if ideas != 1 else ''}")
    return f"{prefix} · {when.strftime('%a %d %b %H:%M')} · " + " · ".join(bits)


def email_html(digest: dict, meta: dict) -> str:
    when = _when(meta)
    body = digest_body_html(digest, meta, palette=LIGHT)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(email_subject(digest, meta))}</title>
</head>
<body style="margin:0;padding:24px 14px;background:{LIGHT['page_bg']}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{LIGHT['page_bg']}">
<tr><td align="center">
<div style="background:#ffffff;border:1px solid {LIGHT['line']};border-radius:14px;padding:24px 26px;max-width:700px;width:100%;text-align:left">
{body}
</div>
<div style="font-family:{FONT};font-size:11px;color:{LIGHT['muted']};margin-top:12px">
Sent {when.strftime('%Y-%m-%d %H:%M %Z')} by agent-monitor.
</div>
</td></tr></table>
</body></html>"""


def email_text(digest: dict, meta: dict) -> str:
    when = _when(meta)
    lines = [f"{meta.get('prefix') or 'Secretary'} · {when.strftime('%a %d %b %Y %H:%M')}", ""]
    lines.append(digest.get("headline") or "")
    lines.append("")
    if digest.get("needs_you"):
        lines.append("NEEDS YOU")
        for entry in digest["needs_you"]:
            lines.append(f"- {entry.get('title')}")
            if entry.get("why"):
                lines.append(f"  {entry['why']}")
            if entry.get("link"):
                lines.append(f"  {entry['link']}")
        lines.append("")
    if digest.get("projects"):
        lines.append("PROJECTS")
        for project in digest["projects"]:
            lines.append(f"* {project.get('name')} — {project.get('status_line')}".rstrip(" —"))
            for bullet in project.get("bullets") or []:
                lines.append(f"  - {bullet}")
        lines.append("")
    if digest.get("ideas"):
        lines.append("IDEAS")
        for idea in digest["ideas"]:
            lines.append(f"- [{idea.get('effort')}] {idea.get('title')} ({idea.get('area')})")
            if idea.get("why_now"):
                lines.append(f"  {idea['why_now']}")
            if idea.get("link"):
                lines.append(f"  {idea['link']}")
        lines.append("")
    if digest.get("risks"):
        lines.append("RISKS")
        lines.extend(f"- {risk}" for risk in digest["risks"])
        lines.append("")
    if digest.get("next_actions"):
        lines.append("NEXT ACTIONS")
        lines.extend(f"{i}. {action}" for i, action in enumerate(digest["next_actions"], 1))
    lines.append("")
    lines.append(
        f"-- agent-monitor · {meta.get('model') or meta.get('provider') or ''} · "
        f"{meta.get('duration_s', 0):.1f}s"
        if isinstance(meta.get("duration_s"), (int, float))
        else "-- agent-monitor"
    )
    return "\n".join(lines)


def to_markdown(digest: dict, meta: dict) -> str:
    when = _when(meta)
    out = [
        f"# {meta.get('prefix') or 'Secretary'} · {when.strftime('%a %d %b %Y, %H:%M')}",
        "",
        f"**{digest.get('headline', '')}**",
        "",
    ]
    if digest.get("needs_you"):
        out.append("## Needs you")
        for entry in digest["needs_you"]:
            link = f" ([link]({entry['link']}))" if safe_url(entry.get("link", "")) else ""
            out.append(f"- **{entry.get('title')}**{link} — {entry.get('why', '')}".rstrip(" —"))
        out.append("")
    if digest.get("projects"):
        out.append("## Projects")
        for project in digest["projects"]:
            out.append(f"### {project.get('name')}")
            if project.get("status_line"):
                out.append(project["status_line"])
            out.extend(f"- {bullet}" for bullet in project.get("bullets") or [])
            out.append("")
    if digest.get("ideas"):
        out.append("## Ideas")
        for idea in digest["ideas"]:
            link = f" ([link]({idea['link']}))" if safe_url(idea.get("link", "")) else ""
            out.append(
                f"- **{idea.get('title')}**{link} — *{idea.get('area')} · {idea.get('effort')} effort*"
                f". {idea.get('why_now', '')}"
            )
        out.append("")
    if digest.get("risks"):
        out.append("## Risks")
        out.extend(f"- {risk}" for risk in digest["risks"])
        out.append("")
    if digest.get("next_actions"):
        out.append("## Next actions")
        out.extend(f"{i}. {action}" for i, action in enumerate(digest["next_actions"], 1))
        out.append("")
    out.append(
        f"---\n_agent-monitor · {meta.get('model') or meta.get('provider') or ''} · "
        f"{meta.get('counts', {}).get('scanned', 0)} items scanned_"
    )
    return "\n".join(out)


def write_digest(cfg, digest: dict, meta: dict) -> dict[str, pathlib.Path]:
    directory = cfg.ensure_dir("paths.digest_dir", "digests")
    stamp = _when(meta).strftime("%Y-%m-%dT%H%M")
    paths = {
        "md": directory / f"{stamp}.md",
        "html": directory / f"{stamp}.html",
        "json": directory / f"{stamp}.json",
    }
    paths["md"].write_text(to_markdown(digest, meta), encoding="utf-8")
    paths["html"].write_text(email_html(digest, meta), encoding="utf-8")
    payload = {"digest": digest, "meta": {**meta, "when": _when(meta).isoformat()}}
    paths["json"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return paths


def load_digest(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def page(title: str, body: str, *, active: str = "", dashboard_title: str = "Secretary") -> str:
    tabs = [
        ("/", "Latest", "latest"),
        ("/chat", "Chat", "chat"),
        ("/history", "History", "history"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for href, label, key in tabs
    )
    full_title = dashboard_title if title == dashboard_title else f"{dashboard_title} · {title}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
{THEME_BOOT}
<style>{PAGE_CSS}</style>
</head><body>
<header>
  <div class="row" style="gap:12px">
    <a class="brand" href="/">{esc(dashboard_title)}</a>
    <form method="post" action="/run" style="margin:0">
      <button class="primary" type="submit" title="Collect and generate a briefing now">Run now</button>
    </form>
    <button class="ghost" id="theme-toggle" type="button" title="Theme: auto follows the device">Auto</button>
  </div>
  <nav>{nav}</nav>
</header>
<main>{body}</main>
{THEME_TOGGLE_JS}
</body></html>"""


def _when_short(value) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %H:%M")
    except ValueError:
        return str(value)[:16]


def _pill(status: str) -> str:
    css = {"open": "warn", "acknowledged": "ok", "accepted": "ok", "dismissed": ""}.get(status, "")
    return f'<span class="pill {css}">{esc(status)}</span>'


def _alert_rows(alerts: list[dict]) -> str:
    rows = []
    for alert in alerts:
        link = safe_url(alert.get("link") or "")
        title = (
            f'<a href="{esc(link)}">{esc(alert.get("title"))}</a>'
            if link
            else esc(alert.get("title"))
        )
        why = (
            f'<div class="muted" style="font-size:13px;margin-top:2px">{esc(alert.get("why"))}</div>'
            if alert.get("why")
            else ""
        )
        actions = [
            f'<a class="linkbtn" href="/chat?thread=alert:{quote(alert["id"])}">Discuss</a>'
        ]
        if alert.get("status") == "dismissed":
            actions.append(
                f'<form method="post" action="/history/alert/{alert["id"]}/reopen">'
                '<button class="ghost" type="submit">Reopen</button></form>'
            )
        else:
            if alert.get("status") != "acknowledged":
                actions.append(
                    f'<form method="post" action="/history/alert/{alert["id"]}/ack">'
                    '<button class="ghost" type="submit">Ack</button></form>'
                )
            actions.append(
                f'<form method="post" action="/history/alert/{alert["id"]}/dismiss">'
                '<button class="ghost" type="submit">Dismiss</button></form>'
            )
        rows.append(
            '<div class="tl-item"><div class="tl-main">'
            f'<div class="tl-title"><span class="kind {esc(alert.get("kind"))}">'
            f'{esc(alert.get("kind"))}</span> {title} {_pill(alert.get("status"))}</div>'
            f"{why}"
            f'<div class="tl-meta">first seen {esc(_when_short(alert.get("first_seen")))} · '
            f'last seen {esc(_when_short(alert.get("last_seen")))} · '
            f'seen {esc(alert.get("seen_count") or 1)}×</div></div>'
            f'<div class="tl-actions">{"".join(actions)}</div></div>'
        )
    return "".join(rows)


def _idea_rows(ideas: list[dict]) -> str:
    rows = []
    for idea in ideas:
        link = safe_url(idea.get("link") or "")
        title = (
            f'<a href="{esc(link)}">{esc(idea.get("title"))}</a>'
            if link
            else esc(idea.get("title"))
        )
        why = (
            f'<div class="muted" style="font-size:13px;margin-top:2px">{esc(idea.get("why_now"))}</div>'
            if idea.get("why_now")
            else ""
        )
        actions = [
            f'<a class="linkbtn" href="/chat?thread=idea:{quote(idea["id"])}">Discuss</a>'
        ]
        if idea.get("status") == "dismissed":
            actions.append(
                f'<form method="post" action="/history/idea/{idea["id"]}/reopen">'
                '<button class="ghost" type="submit">Reopen</button></form>'
            )
        else:
            if idea.get("status") != "accepted":
                actions.append(
                    f'<form method="post" action="/history/idea/{idea["id"]}/accept">'
                    '<button class="ghost" type="submit">Accept</button></form>'
                )
            actions.append(
                f'<form method="post" action="/history/idea/{idea["id"]}/dismiss">'
                '<button class="ghost" type="submit">Dismiss</button></form>'
            )
        rows.append(
            '<div class="tl-item"><div class="tl-main">'
            f'<div class="tl-title"><span class="kind">{esc(idea.get("effort") or "M")} effort</span> '
            f'{title} {_pill(idea.get("status"))}</div>{why}'
            f'<div class="tl-meta">{esc(idea.get("area"))} · '
            f'first seen {esc(_when_short(idea.get("created_at")))} · '
            f'last seen {esc(_when_short(idea.get("last_seen")))} · '
            f'seen {esc(idea.get("seen_count") or 1)}×</div></div>'
            f'<div class="tl-actions">{"".join(actions)}</div></div>'
        )
    return "".join(rows)


def _details(summary: str, rows: str) -> str:
    if not rows:
        return ""
    return (
        f'<details style="margin-top:10px"><summary class="muted" '
        f'style="cursor:pointer;font-size:12px">{esc(summary)}</summary>'
        f'<div class="tl" style="margin-top:8px">{rows}</div></details>'
    )


def _runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<div class="empty">No runs yet.</div>'
    rows = []
    for run in runs:
        status = run.get("status") or ""
        pill_class = {"ok": "ok", "error": "err", "running": "warn"}.get(status, "")
        duration = ""
        if run.get("finished_at") and run.get("started_at"):
            try:
                start = datetime.fromisoformat(run["started_at"])
                end = datetime.fromisoformat(run["finished_at"])
                duration = f"{(end - start).total_seconds():.1f}s"
            except ValueError:
                duration = ""
        started = (run.get("started_at") or "")[:16].replace("T", " ")
        error = (
            f'<div class="muted" style="margin-top:4px">{esc(str(run.get("error"))[:200])}</div>'
            if run.get("error")
            else ""
        )
        rows.append(
            "<tr>"
            f"<td>{esc(started)}</td>"
            f'<td><span class="pill {pill_class}">{esc(status)}</span>{error}</td>'
            f'<td>{esc(run.get("provider") or "")}<div class="muted">{esc(run.get("model") or "")}</div></td>'
            f'<td>{esc(run.get("items_new") or 0)} new<br><span class="muted">'
            f'{esc(run.get("items_changed") or 0)} changed</span></td>'
            f'<td>{esc(run.get("ideas_new") or 0)}</td>'
            f'<td class="muted">{esc(duration)}</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr><th style="white-space:nowrap">Started (UTC)</th><th>Status</th>'
        "<th>Model</th><th>Items</th><th>Ideas</th><th>Took</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def history_page(alerts: list[dict], ideas: list[dict], runs: list[dict]) -> str:
    active_alerts = [alert for alert in alerts if alert.get("status") != "dismissed"]
    old_alerts = [alert for alert in alerts if alert.get("status") == "dismissed"]
    active_ideas = [idea for idea in ideas if idea.get("status") != "dismissed"]
    old_ideas = [idea for idea in ideas if idea.get("status") == "dismissed"]

    alert_body = _alert_rows(active_alerts) or '<div class="empty">No attention points yet.</div>'
    idea_body = _idea_rows(active_ideas) or '<div class="empty">No ideas yet.</div>'

    body = f"""
<div class="card" id="alerts" style="margin-bottom:14px">
  <div class="row" style="margin-bottom:6px">
    <h2 style="margin:0;font-size:16px">Attention points</h2>
    <span class="muted">{len(active_alerts)} active · {len(alerts)} in history</span>
  </div>
  <div class="muted" style="font-size:12px;margin-bottom:8px">
    Every point of attention raised by a briefing, kept until you ack or dismiss it.
  </div>
  <div class="tl">{alert_body}</div>
  {_details(f"Dismissed ({len(old_alerts)})", _alert_rows(old_alerts))}
</div>
<div class="card" id="ideas" style="margin-bottom:14px">
  <div class="row" style="margin-bottom:6px">
    <h2 style="margin:0;font-size:16px">Ideas</h2>
    <span class="muted">{len(active_ideas)} active · {len(ideas)} in history</span>
  </div>
  <div class="muted" style="font-size:12px;margin-bottom:8px">
    New suggestions are checked against this list, so the same idea is never proposed twice.
  </div>
  <div class="tl">{idea_body}</div>
  {_details(f"Dismissed ({len(old_ideas)})", _idea_rows(old_ideas))}
</div>
<div class="card" id="runs">
  <div class="row" style="margin-bottom:10px">
    <h2 style="margin:0;font-size:16px">Runs</h2>
    <span class="muted">{len(runs)} most recent</span>
  </div>
  {_runs_table(runs)}
</div>"""
    return page("History", body, active="history")


def latest_page(payload: dict | None, run: dict | None = None, banner: str = "") -> str:
    banner_html = f'<div class="card" style="margin-bottom:14px">{banner}</div>' if banner else ""
    if not payload:
        body = (
            banner_html
            + '<div class="card"><div class="empty">'
            "<p>No briefing yet.</p>"
            "<p>Click “Run now” or run <code>agent-monitor run</code>.</p>"
            "</div></div>"
        )
        return page("Latest", body, active="latest")
    digest = payload.get("digest") or {}
    meta = payload.get("meta") or {}
    body = (
        banner_html
        + '<div class="card">'
        + digest_body_html(digest, meta, palette=PAGE, interactive=True)
        + "</div>"
    )
    return page(digest.get("headline") or "Latest", body, active="latest")


def chat_page(
    thread: str,
    messages: list[dict],
    label: str,
    *,
    dashboard_title: str = "Secretary",
    notes: int = 0,
    ideas: int = 0,
) -> str:
    bubbles = []
    for message in messages:
        role = message.get("role") or "assistant"
        status = message.get("status") or "ok"
        css = "user" if role == "user" else "assistant"
        text = message.get("content") or ""
        if status == "pending":
            css += " pending"
            text = "thinking…"
        elif status == "error":
            css += " error"
        bubbles.append(f'<div class="msg {css}">{esc(text)}</div>')
    box = "".join(bubbles) or (
        '<div class="msg assistant">Ask me about any item, or tell me to remember an idea '
        'or a priority. Notes and ideas from here feed the next briefing.</div>'
    )
    thread_json = json.dumps(thread)
    general_chip = (
        f'<a href="/chat" class="{"active" if thread == "general" else ""}">General</a>'
    )
    item_chip = (
        f'<a href="/chat?thread={quote(thread)}" class="active">{esc(label)}</a>'
        if thread != "general"
        else ""
    )
    body = f"""
<div class="card">
  <div class="chat-head">
    <div>
      <div class="chat-thread">{esc(label)}</div>
      <div class="muted" style="font-size:12px">
        {esc(notes)} note(s) and {esc(ideas)} tracked idea(s) shape future briefings.
      </div>
    </div>
    <div class="chips">{general_chip}{item_chip}</div>
  </div>
  <div class="chat-box" id="messages">{box}</div>
  <form class="chat-form" id="chat-form" method="post" action="/chat">
    <input type="hidden" name="thread" value="{esc(thread)}">
    <textarea name="message" placeholder="Ask about an item, think out loud, or say “remember this idea: …”" required></textarea>
    <button class="primary" type="submit">Send</button>
  </form>
  <div class="chat-foot">
    <form method="post" action="/run" style="margin:0">
      <button class="ghost" type="submit">Regenerate briefing</button>
    </form>
    <span class="muted" style="font-size:12px">Regenerate collects fresh GitHub data and emails the new briefing.</span>
  </div>
</div>
<script>
(function(){{
  var thread = {thread_json};
  var form = document.getElementById('chat-form');
  var box = document.getElementById('messages');
  var input = form.querySelector('textarea');
  function bubble(role, status, text) {{
    var d = document.createElement('div');
    d.className = 'msg ' + (role === 'user' ? 'user' : 'assistant')
      + (status === 'pending' ? ' pending' : '')
      + (status === 'error' ? ' error' : '');
    d.textContent = text;
    return d;
  }}
  function render(messages) {{
    box.textContent = '';
    messages.forEach(function(m) {{
      var text = m.status === 'pending' ? 'thinking…' : (m.content || '');
      box.appendChild(bubble(m.role, m.status, text));
    }});
    box.scrollTop = box.scrollHeight;
  }}
  async function refresh() {{
    var response = await fetch('/api/chat?thread=' + encodeURIComponent(thread));
    var data = await response.json();
    render(data.messages || []);
    return (data.messages || []).some(function(m) {{ return m.status === 'pending'; }});
  }}
  form.addEventListener('submit', async function(event) {{
    event.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    box.appendChild(bubble('user', 'ok', text));
    box.appendChild(bubble('assistant', 'pending', 'thinking…'));
    box.scrollTop = box.scrollHeight;
    await fetch('/api/chat', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{thread: thread, message: text}})
    }});
    while (await refresh()) {{
      await new Promise(function(resolve) {{ setTimeout(resolve, 2500); }});
    }}
  }});
  if (document.querySelector('.msg.pending')) {{
    (async function() {{
      while (await refresh()) {{
        await new Promise(function(resolve) {{ setTimeout(resolve, 2500); }});
      }}
    }})();
  }}
}})();
</script>"""
    return page("Chat", body, active="chat", dashboard_title=dashboard_title)


def message_page(title: str, message: str) -> str:
    body = (
        f'<div class="card"><h2 style="margin:0 0 8px;font-size:16px">{esc(title)}</h2>'
        f'<p class="muted">{esc(message)}</p></div>'
    )
    return page(title, body)
