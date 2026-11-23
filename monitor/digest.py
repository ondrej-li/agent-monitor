"""Prompt construction, response parsing and idea de-duplication."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "to",
    "we",
    "when",
    "with",
    "would",
    "you",
}

SYSTEM_PROMPT = """\
You are the personal engineering secretary for {name} (GitHub login: {login}).
You read a machine-collected snapshot of his GitHub work and write a short briefing
that he can read in two minutes, plus a small number of concrete ideas.

Context about his work:
{bio}

Return ONE JSON object, no prose, no markdown fences, matching exactly:
{{
  "headline": "one sentence, <= 110 characters, the state of play",
  "needs_you": [{{"title": "...", "why": "...", "link": "https://..."}}],
  "projects": [{{"name": "owner/repo or theme", "status_line": "...", "bullets": ["..."]}}],
  "ideas": [{{"title": "...", "area": "owner/repo or theme", "why_now": "...", "effort": "S|M|L", "link": "https://..."}}],
  "risks": ["..."],
  "next_actions": ["..."]
}}

Rules:
- Use ONLY the supplied data. Never invent PR/issue numbers, titles or links.
- Do not claim CI results, merge conflicts or mergeability unless the field is present
  in the data. If you are unsure, leave it out.
- Report change, not history: what happened since "since" and what now needs a decision.
- needs_you: only things where he is the bottleneck — a review he owes, a stale PR of his,
  failing checks on his PR, an unanswered question, an item blocked on his decision.
  Keep each "why" under 140 characters. Max 6 entries. Prefer recent work: an item idle
  more than 45 days belongs to "parked", not here.
- parked: long-abandoned repositories of items. They are background cleanup, lowest
  priority. Mention them in at most one idea or one line in total, never in the headline.
- projects: max 6 entries, most important first. Two to four short bullets each.
  Mention repository names so they are scannable.
- ideas: 0-4. Quality over quantity: return 0-2 when the window is quiet, and 0 is a
  valid answer when nothing is worth suggesting. Concrete, specific and implementable
  within a week. Explain why now.
  - For work projects the idea must stay in his domain: {domains}.
  - For his personal projects any sensible idea is fine.
  - An idea outside his domain is allowed only when it is an obvious, cheap (<1 day)
    high-value win; say so in "why_now".
  - NEVER repeat or paraphrase an idea from "already_suggested". Build on it (next step,
    extension) or leave it out. Repetition is the main failure mode.
  - Do not offer another variation on a theme that is already in "already_suggested".
    If you only have variations, return fewer ideas instead.
- user_notes are his own statements of priority and context (from chat). Respect them:
  they should shape ideas and next_actions, and must never be contradicted.
- risks: real risks only (data loss, security, flaky CI, drift), max 3. Omit if none.
- next_actions: max 5, imperative, ordered by payoff.
- Be terse. No praise, no filler, no restating the input.
"""


def projected_item(item: dict, details: dict | None = None, *, include_url: bool = True) -> dict:
    projected = {
        "kind": item["kind"],
        "repo": item["repo"],
        "number": item["number"],
        "title": item["title"],
        "state": item["state"],
        "involvement": sorted(item.get("involvement") or []),
        "idle_days": item.get("idle_days", 0),
        "comments": item.get("comments", 0),
    }
    if item.get("is_draft"):
        projected["draft"] = True
    if item.get("review_decision"):
        projected["review_decision"] = item["review_decision"]
    if item.get("labels"):
        projected["labels"] = item["labels"][:5]
    detail = (details or {}).get(item["key"])
    if detail:
        checks = detail.get("checks") or {}
        if checks.get("state") and checks["state"] != "none":
            projected["checks"] = checks["state"]
        if checks.get("failing"):
            projected["checks_failing"] = checks["failing"]
        if detail.get("mergeable"):
            projected["mergeable"] = detail["mergeable"]
        projected["diff"] = (
            f"+{detail.get('additions', 0)}/-{detail.get('deletions', 0)}"
            f" in {detail.get('changed_files', 0)} files"
        )
    if include_url:
        projected["url"] = item.get("url", "")
    return projected


def _cap(sequence, limit):
    return sequence[:limit]


def build_context(
    cfg,
    collection: dict,
    diff: dict,
    board: list[dict],
    suggestions: list[dict],
    now: datetime,
    notes: list[dict] | None = None,
) -> dict:
    details = collection.get("details", {})
    login = collection["login"]
    stale_days = int(cfg.get("general.stale_after_days", 7))
    max_items = int(cfg.get("github.max_items", 60))

    open_new = [projected_item(item, details) for item in diff["new"] if item["state"] == "open"]
    changed_open = [
        projected_item(item, details) for item in diff["changed"] if item["state"] == "open"
    ]
    closed_new = [projected_item(item, details) for item in diff["new"] if item["state"] != "open"]
    closed_changed = [
        projected_item(item, details) for item in diff["changed"] if item["state"] != "open"
    ]

    seen = {item["key"] for item in diff["new"]} | {item["key"] for item in diff["changed"]}
    idle_candidates = [
        item
        for item in board
        if item["key"] not in seen and item.get("idle_days", 0) >= stale_days
    ]
    fresh_idle = [item for item in idle_candidates if item["idle_days"] <= 45]
    fresh_idle.sort(key=lambda item: (item["priority"], -item["idle_days"]))
    ancient = [item for item in idle_candidates if item["idle_days"] > 45]
    parked: dict[str, dict] = {}
    for item in ancient:
        entry = parked.setdefault(item["repo"], {"repo": item["repo"], "count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < 3:
            entry["examples"].append(f"#{item['number']} {item['title'][:70]}")
    parked_list = sorted(parked.values(), key=lambda entry: -entry["count"])[:6]

    commits_by_repo: dict[str, list[dict]] = {}
    for commit in collection.get("commits", []):
        commits_by_repo.setdefault(commit["repo"], []).append(commit)
    activity = []
    for repo, commits in sorted(commits_by_repo.items(), key=lambda kv: -len(kv[1])):
        activity.append(
            {
                "repo": repo,
                "commits": len(commits),
                "recent": [commit["message"] for commit in commits[:4]],
            }
        )

    return {
        "now": now.isoformat(),
        "since": collection["window"]["since"],
        "identity": {
            "login": login,
            "name": cfg.get("identity.name") or login,
            "bio": cfg.get("identity.bio") or "",
        },
        "stats": {
            "repos_scanned": len(collection.get("repos", [])),
            "open_items": len(board),
            "new": len(diff["new"]),
            "changed": len(diff["changed"]),
            "commits": len(collection.get("commits", [])),
        },
        "new": _cap(open_new, max_items),
        "changed": _cap(changed_open, max_items),
        "closed": _cap(closed_new + closed_changed, max_items),
        "idle": _cap([projected_item(item, details) for item in fresh_idle], 12),
        "parked": parked_list,
        "activity": _cap(activity, 12),
        "repos": [
            {"name": repo["name"], "idle_days": repo["idle_days"]}
            for repo in collection.get("repos", [])
            if repo["priority"] <= 1
        ][:25],
        "user_notes": [note["text"] for note in (notes or [])][:8],
        "already_suggested": [
            {
                "title": suggestion["title"],
                "area": suggestion.get("area", ""),
                "status": suggestion.get("status", ""),
            }
            for suggestion in suggestions[:60]
        ],
        "_raw": collection,
        "_diff": diff,
        "_board": board,
    }


def build_prompt(context: dict) -> tuple[str, str]:
    identity = context["identity"]
    domains = cfg.get("identity.domains") or []
    system = SYSTEM_PROMPT.format(
        name=identity["name"],
        login=identity["login"],
        bio=identity["bio"] or "(not provided)",
        domains=", ".join(str(domain) for domain in domains) if domains else "his stated areas of work",
    )
    payload = {key: value for key, value in context.items() if not key.startswith("_")}
    user = (
        "Here is the snapshot. Write the briefing for "
        f"{identity['name']}.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    return system, user


def parse_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("model response contained no JSON object")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model response was not a JSON object")
    return data


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _list_of_texts(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(entry) for entry in value) if text][:limit]


def normalize(data: dict) -> dict:
    needs = []
    for entry in data.get("needs_you") or []:
        if isinstance(entry, dict) and _text(entry.get("title")):
            needs.append(
                {
                    "title": _text(entry.get("title"))[:200],
                    "why": _text(entry.get("why"))[:280],
                    "link": _text(entry.get("link")),
                }
            )
    projects = []
    for entry in data.get("projects") or []:
        if isinstance(entry, dict) and _text(entry.get("name")):
            projects.append(
                {
                    "name": _text(entry.get("name"))[:120],
                    "status_line": _text(entry.get("status_line"))[:220],
                    "bullets": _list_of_texts(entry.get("bullets"), 5),
                }
            )
    ideas = []
    for entry in data.get("ideas") or []:
        if isinstance(entry, dict) and _text(entry.get("title")):
            effort = _text(entry.get("effort")).upper()[:1]
            ideas.append(
                {
                    "title": _text(entry.get("title"))[:200],
                    "area": _text(entry.get("area"))[:120],
                    "why_now": _text(entry.get("why_now"))[:320],
                    "effort": effort if effort in {"S", "M", "L"} else "M",
                    "link": _text(entry.get("link")),
                }
            )
    return {
        "headline": _text(data.get("headline"))[:160],
        "needs_you": needs[:6],
        "projects": projects[:6],
        "ideas": ideas[:4],
        "risks": _list_of_texts(data.get("risks"), 3),
        "next_actions": _list_of_texts(data.get("next_actions"), 5),
    }


def tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def idea_text(idea: dict) -> str:
    return " ".join(
        [idea.get("title") or "", idea.get("area") or "", idea.get("why_now") or ""]
    )


def suggestion_text(suggestion: dict) -> str:
    return " ".join(
        [
            suggestion.get("title") or "",
            suggestion.get("area") or "",
            suggestion.get("why_now") or "",
        ]
    )


def fingerprint(idea: dict) -> str:
    key = " ".join(sorted(tokens(f"{idea.get('title', '')} {idea.get('area', '')}")))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def dedupe_ideas(ideas: list[dict], existing: list[dict], threshold: float = 0.45) -> tuple[list[dict], list[dict]]:
    """Split ideas into new ones and repeats of anything already suggested."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for idea in ideas:
        text = idea_text(idea)
        matches = [suggestion for suggestion in existing if similarity(text, suggestion_text(suggestion)) >= threshold]
        matches += [prior for prior in kept if similarity(text, idea_text(prior)) >= threshold]
        if matches:
            dropped.append({"idea": idea, "matches": matches})
        else:
            kept.append(idea)
    return kept, dropped


def allowed_urls(context: dict) -> set[str]:
    urls: set[str] = set()
    for group in ("new", "changed", "closed", "idle"):
        for item in context.get(group) or []:
            url = (item.get("url") or "").rstrip("/")
            if url:
                urls.add(url)
    return urls


def enforce_links(briefing: dict, context: dict) -> dict:
    """Drop any link the model invented; keep the claim, lose the bogus target."""
    allowed = allowed_urls(context)
    entries = list(briefing.get("needs_you") or []) + list(briefing.get("ideas") or [])
    for entry in entries:
        link = (entry.get("link") or "").rstrip("/")
        entry["link"] = link if link in allowed else ""
    return briefing


def fallback_digest(context: dict) -> dict:
    """Deterministic briefing used when the model is unavailable."""
    needs = []
    for item in context["new"] + context["changed"]:
        if item["state"] != "open":
            continue
        if "review requested" in item["involvement"]:
            needs.append(
                {
                    "title": f"{item['repo']}#{item['number']} waits for your review",
                    "why": "Review requested and still open.",
                    "link": item.get("url", ""),
                }
            )
        elif item.get("checks") == "failing":
            needs.append(
                {
                    "title": f"{item['repo']}#{item['number']} has failing checks",
                    "why": f"{item.get('checks_failing', 0)} failing check(s) on your PR.",
                    "link": item.get("url", ""),
                }
            )
    for item in context["idle"]:
        needs.append(
            {
                "title": f"{item['repo']}#{item['number']} has been idle {item['idle_days']}d",
                "why": "No activity; it may be waiting on you.",
                "link": item.get("url", ""),
            }
        )

    projects = [
        {
            "name": entry["repo"],
            "status_line": f"{entry['commits']} commit(s) since the last briefing",
            "bullets": entry["recent"],
        }
        for entry in context["activity"][:4]
    ]

    stats = context["stats"]
    return normalize(
        {
            "headline": (
                f"{stats['open_items']} open items, {stats['new']} new and "
                f"{stats['changed']} changed; {stats['commits']} commits."
            ),
            "needs_you": needs[:6],
            "projects": projects,
            "ideas": [],
            "risks": [],
            "next_actions": [entry["title"] for entry in needs[:5]],
        }
    )
