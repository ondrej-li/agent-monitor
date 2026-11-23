"""Two-way chat about tracked work, stored as JSONL files."""

from __future__ import annotations

import json
import pathlib
import re
from . import digest as digest_mod, llm, store as store_mod

SYSTEM = """\
You are the secretary for {name} (GitHub login: {login}). You wrote his latest briefing and
now you are talking with him inside the dashboard.

He can ask about an item, think out loud, ask you to remember an idea, or state a priority
that future briefings should respect.

Rules:
- Be concise: one short paragraph or a few bullets, at most ~200 words. No praise, no filler.
- Ground every statement in the provided context. If it is not there, say so; never invent
  PR/issue numbers, CI results, merge status or dates.
- `focus` is what this thread is about (an idea, an alert, an item, or null for general chat).
  Answer about it first, then widen to related work.
- Alerts are the ongoing points of attention; ideas are tracked suggestions. Their status and
  history (first seen, last seen, how often) are in the context — use them.
- Emit actions ONLY when he asks you to remember something. A question deserves no action.
- Return JSON only, exactly: {{"reply": "...", "actions": []}}
- Allowed actions (at most 2):
  {{"type": "idea", "title": "...", "area": "...", "why_now": "...", "effort": "S|M|L"}}
  {{"type": "note", "text": "..."}}
- A "note" is durable context or a priority for future briefings ("focus on atlas this week").
- If the idea is already in tracked_ideas, do not repeat it: say it is already tracked and
  return no action for it.
- recent_commits and recent_merged are the sources for "what changed"; use them when asked
  about progress, and quote their subjects rather than guessing.
"""

REPLY_RE = re.compile(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)


def open_state(cfg):
    return store_mod.open_store(cfg)


def parse_thread(thread: str) -> tuple[str, str]:
    for prefix in ("item", "idea", "alert"):
        if thread.startswith(prefix + ":"):
            return prefix, thread[len(prefix) + 1 :]
    return "general", ""


def thread_object(state, thread: str) -> dict | None:
    kind, ref = parse_thread(thread)
    if kind == "item":
        return state.item(ref)
    if kind == "idea":
        return state.idea(ref)
    if kind == "alert":
        return state.alert(ref)
    return None


def thread_label(state, thread: str) -> str:
    if not thread or thread == "general":
        return "General"
    kind, ref = parse_thread(thread)
    obj = thread_object(state, thread)
    if obj:
        title = (obj.get("title") or ref)[:80]
        if kind == "item":
            return f"{obj.get('repo')}#{obj.get('number')} · {title[:70]}"
        return f"{kind.title()} · {title}"
    return f"{kind.title()} · {ref}"


def _latest_digest(state) -> dict | None:
    for run in state.runs(5):
        path = run.get("digest_path")
        if not path:
            continue
        try:
            return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _trim_digest(payload: dict | None) -> dict | None:
    if not payload:
        return None
    digest = payload.get("digest") or {}
    meta = payload.get("meta") or {}
    return {
        "when": meta.get("when"),
        "headline": digest.get("headline"),
        "needs_you": [
            {"title": entry.get("title"), "why": entry.get("why"), "link": entry.get("link")}
            for entry in (digest.get("needs_you") or [])[:6]
        ],
        "projects": [
            {
                "name": project.get("name"),
                "status_line": project.get("status_line"),
                "bullets": (project.get("bullets") or [])[:4],
            }
            for project in (digest.get("projects") or [])[:6]
        ],
        "ideas": [
            {"title": idea.get("title"), "area": idea.get("area"), "why_now": idea.get("why_now")}
            for idea in (digest.get("ideas") or [])[:4]
        ],
        "next_actions": (digest.get("next_actions") or [])[:5],
    }


def _trim_alert(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "title": record.get("title"),
        "why": record.get("why"),
        "link": record.get("link"),
        "status": record.get("status"),
        "seen_count": record.get("seen_count"),
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
    }


def _trim_idea(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "area": record.get("area"),
        "why_now": record.get("why_now"),
        "effort": record.get("effort"),
        "status": record.get("status"),
        "seen_count": record.get("seen_count"),
        "first_seen": record.get("created_at"),
        "last_seen": record.get("last_seen"),
    }


def build_context(cfg, state, thread: str) -> dict:
    kind, ref = parse_thread(thread)
    focus = thread_object(state, thread)
    repo = ""
    if focus:
        repo = focus.get("repo") or ""
    related = [row for row in state.open_items() if not repo or row.get("repo") == repo][:25]
    related_commits = state.recent_commits(limit=12 if focus else 25, repo=repo or None)
    history = [
        {"role": message["role"], "content": message["content"]}
        for message in state.messages(thread, limit=10)
        if message.get("status") == "ok"
    ]
    return {
        "now": store_mod.now_iso(),
        "login": cfg.get("identity.login"),
        "thread": thread,
        "focus": (
            {"kind": kind, "record": _trim_alert(focus) if kind == "alert" else _trim_idea(focus) if kind == "idea" else focus}
            if focus
            else None
        ),
        "related_items": related,
        "briefing": _trim_digest(_latest_digest(state)),
        "open_items": [
            {
                "key": row.get("key"),
                "repo": row.get("repo"),
                "number": row.get("number"),
                "title": row.get("title"),
                "idle_days": row.get("idle_days"),
                "involvement": row.get("involvement"),
            }
            for row in state.open_items()[:25]
        ],
        "tracked_ideas": [_trim_idea(record) for record in state.active_ideas()[:12]],
        "active_alerts": [
            _trim_alert(record)
            for record in state.alerts()
            if record.get("status") in ("open", "acknowledged")
        ][:12],
        "recent_merged": [
            {
                "repo": row.get("repo"),
                "number": row.get("number"),
                "title": row.get("title"),
                "state": row.get("state"),
                "updated_at": row.get("updated_at"),
            }
            for row in sorted(
                (item for item in state.items().values() if item.get("state") != "open"),
                key=lambda item: item.get("updated_at") or "",
                reverse=True,
            )[:10]
        ],
        "recent_commits": related_commits,
        "notes": [note["text"] for note in state.notes(8)],
        "history": history,
    }


def build_prompt(cfg, context: dict) -> tuple[str, str]:
    name = cfg.get("identity.name") or context.get("login") or "Ondrej"
    system = SYSTEM.format(name=name, login=context.get("login") or "the user")
    user = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return system, user


def parse(text: str) -> dict:
    data = digest_mod.parse_response(text)
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("model returned no reply")
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []
    return {"reply": reply.strip(), "actions": actions[:2]}


def fallback_reply(raw: str) -> str:
    """Recover a usable answer when the model did not return valid JSON."""
    text = (raw or "").strip()
    match = REPLY_RE.search(text)
    if match:
        try:
            return json.loads('"' + match.group(1) + '"')[:4000]
        except json.JSONDecodeError:
            return match.group(1)[:4000]
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text[:4000]


def _confirmation(applied: list[dict]) -> str:
    saved_ideas = sum(1 for action in applied if action["type"] == "idea" and action["status"] == "saved")
    saved_notes = sum(1 for action in applied if action["type"] == "note" and action["status"] == "saved")
    duplicates = sum(1 for action in applied if action["status"] == "duplicate")
    bits = []
    if saved_ideas:
        bits.append(f"{saved_ideas} idea{'s' if saved_ideas > 1 else ''} tracked")
    if saved_notes:
        bits.append(f"{saved_notes} note{'s' if saved_notes > 1 else ''} saved for the next briefing")
    if duplicates:
        bits.append("already tracked")
    return ("\n\n— " + " · ".join(bits) + ".") if bits else ""


def apply_actions(state, thread: str, actions: list[dict]) -> list[dict]:
    applied: list[dict] = []
    for action in actions[:2]:
        if not isinstance(action, dict):
            continue
        kind = (action.get("type") or "").lower()
        if kind == "idea":
            title = (action.get("title") or "").strip()
            if not title:
                continue
            effort = (action.get("effort") or "M").upper()[:1]
            idea = {
                "title": title[:200],
                "area": (action.get("area") or "").strip()[:120],
                "why_now": (action.get("why_now") or "").strip()[:320],
                "effort": effort if effort in {"S", "M", "L"} else "M",
                "link": "",
            }
            kept, _ = digest_mod.dedupe_ideas([idea], state.active_ideas())
            if not kept:
                applied.append({"type": "idea", "status": "duplicate", "title": idea["title"]})
                continue
            created, record = state.add_idea(idea, digest_mod.fingerprint(idea), source="chat")
            applied.append(
                {
                    "type": "idea",
                    "status": "saved" if created else "duplicate",
                    "title": idea["title"],
                    "id": record.get("id"),
                }
            )
        elif kind == "note":
            text = (action.get("text") or "").strip()
            if not text:
                continue
            state.add_note(text[:500], source=f"chat:{thread}")
            applied.append({"type": "note", "status": "saved", "text": text[:500]})
    return applied


def submit(cfg, thread: str, message: str) -> tuple[str, int]:
    thread = thread or "general"
    state = open_state(cfg)
    kind, ref = parse_thread(thread)
    state.ensure_thread(thread, thread_label(state, thread), kind, ref)
    state.add_message(thread, "user", message.strip())
    assistant_id = state.add_message(thread, "assistant", "", status="pending")
    return thread, assistant_id


def answer(cfg, thread: str, assistant_id: int, log=print) -> dict:
    state = open_state(cfg)
    try:
        context = build_context(cfg, state, thread)
        system, user = build_prompt(cfg, context)
        candidates = [cfg.get("llm.provider", "anthropic")]
        fallback = cfg.get("llm.fallback_provider")
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        raw = None
        used = ""
        errors = ""
        for candidate in candidates:
            try:
                raw = llm.complete(cfg, system, user, provider=candidate)
                used = llm.resolve(cfg, candidate)["name"]
                break
            except Exception as exc:  # noqa: BLE001 - try the next provider
                errors = f"{type(exc).__name__}: {exc}"
                log(f"chat provider {candidate} failed: {errors}")
        if raw is None:
            raise RuntimeError(errors or "no provider available")
        try:
            parsed = parse(raw)
        except Exception as exc:  # noqa: BLE001 - prose beats an error page
            log(f"chat JSON parse failed ({exc}); using the raw answer")
            parsed = {"reply": fallback_reply(raw), "actions": []}
        applied = apply_actions(state, thread, parsed["actions"])
        content = parsed["reply"] + _confirmation(applied)
        state.update_message(thread, assistant_id, content=content, status="ok", actions=applied)
        return {"reply": parsed["reply"], "applied": applied, "provider": used}
    except Exception as exc:  # noqa: BLE001 - the chat must stay usable
        state.update_message(thread, assistant_id, content=f"I couldn't answer: {exc}", status="error")
        return {"error": f"{type(exc).__name__}: {exc}"}


def ask_once(cfg, thread: str, message: str, log=print) -> dict:
    thread, assistant_id = submit(cfg, thread, message)
    return answer(cfg, thread, assistant_id, log=log)
