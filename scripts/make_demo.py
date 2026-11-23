"""Generate a fictional demo store and briefing for screenshots.

Everything here is made up: fictional people, repositories and links. Run it with
`python3 scripts/make_demo.py`, then `agent-monitor --config demo/config.toml serve`.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from monitor import config, render, store  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def reset(path: pathlib.Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def item(repo, number, kind, title, state, updated, idle, involvement, priority=1):
    slug = "pull" if kind == "pr" else "issues"
    return {
        "key": f"{repo}#{number}",
        "repo": repo,
        "kind": kind,
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/{slug}/{number}",
        "state": state,
        "updated_at": iso(updated),
        "created_at": iso(updated - timedelta(days=3)),
        "involvement": involvement,
        "priority": priority,
        "idle_days": idle,
    }


def main() -> None:
    cfg = config.load(ROOT / "demo" / "config.toml")
    reset(cfg.path("paths.store_dir"))
    reset(cfg.path("paths.digest_dir"))
    state = store.Store(cfg.path("paths.store_dir"))
    now = datetime.now(timezone.utc)

    state.record_items(
        [
            item("acme/orbit", 412, "pr", "feat: reconcile invoice totals nightly", "merged", now - timedelta(hours=3), 0, ["author"], 0),
            item("acme/orbit", 418, "pr", "refactor: split the billing sync into stages", "open", now - timedelta(hours=5), 0, ["author", "review requested"], 0),
            item("acme/orbit", 405, "pr", "test: golden files for the reconciliation report", "open", now - timedelta(days=9), 9, ["author"], 0),
            item("acme/ledger", 233, "pr", "fix: drop duplicate entries on import", "open", now - timedelta(days=12), 12, ["review requested"], 1),
            item("acme/ledger", 219, "issue", "Imports above 50k rows time out", "open", now - timedelta(days=4), 4, ["author", "assignee"], 1),
            item("acme/gateway", 88, "pr", "chore: rate-limit the public API", "merged", now - timedelta(days=2), 0, ["author"], 1),
            item("acme/gateway", 91, "issue", "Add a per-key quota endpoint", "open", now - timedelta(days=18), 18, ["assignee"], 1),
            item("demo-user/pixel-picker", 12, "pr", "feat: remember the last selected folder", "merged", now - timedelta(days=1), 0, ["author"], 0),
            item("demo-user/pixel-picker", 14, "issue", "TestFlight build 3 needs release notes", "open", now - timedelta(days=6), 6, ["author"], 0),
            item("demo-user/dropnote", 7, "pr", "feat: offline drafts", "open", now - timedelta(days=21), 21, ["author"], 1),
        ],
        now,
    )

    state.record_commits(
        [
            {"repo": "acme/orbit", "sha": "a1b2c3d4", "message": "feat: reconcile invoice totals nightly (#412)", "date": iso(now - timedelta(hours=3)), "url": "https://github.com/acme/orbit/commit/a1b2c3d4"},
            {"repo": "acme/orbit", "sha": "b2c3d4e5", "message": "test: pin the report format with golden files", "date": iso(now - timedelta(hours=8)), "url": "https://github.com/acme/orbit/commit/b2c3d4e5"},
            {"repo": "acme/gateway", "sha": "c3d4e5f6", "message": "chore: rate-limit the public API (#88)", "date": iso(now - timedelta(days=2)), "url": "https://github.com/acme/gateway/commit/c3d4e5f6"},
            {"repo": "demo-user/pixel-picker", "sha": "d4e5f6a7", "message": "feat: remember the last selected folder (#12)", "date": iso(now - timedelta(days=1)), "url": "https://github.com/demo-user/pixel-picker/commit/d4e5f6a7"},
        ],
        now,
    )

    ideas = []
    for idea in [
        {"title": "Add a dry-run mode to the reconciliation job", "why_now": "The nightly run is now safe to re-run, so a diff preview is cheap to add.", "area": "acme/orbit", "effort": "S"},
        {"title": "Cache ledger imports by content hash to skip re-parsing", "why_now": "Imports above 50k rows time out (#219); most files repeat week to week.", "area": "acme/ledger", "effort": "M"},
        {"title": "Expose per-key quotas in the gateway admin API", "why_now": "Issue #91 has been idle 18 days and blocks the self-serve onboarding.", "area": "acme/gateway", "effort": "M"},
        {"title": "Add a TestFlight release checklist to pixel-picker", "why_now": "Build 3 is waiting on release notes (#14).", "area": "demo-user/pixel-picker", "effort": "S"},
    ]:
        created, record = state.add_idea(idea, f"demo-{len(ideas)}", source="digest")
        ideas.append(record)
    state.set_idea_status(ideas[3]["id"], "accepted")

    needs = [
        {
            "title": "acme/orbit #418: review the billing sync refactor",
            "why": "Review requested; the staged split is ready to read.",
            "link": "https://github.com/acme/orbit/pull/418",
        },
        {
            "title": "acme/ledger #233: duplicate entries on import",
            "why": "Review requested 12 days ago and still open.",
            "link": "https://github.com/acme/ledger/pull/233",
        },
        {
            "title": "pixel-picker #14: TestFlight release notes",
            "why": "Build 3 cannot ship until the notes exist.",
            "link": "https://github.com/demo-user/pixel-picker/issues/14",
        },
    ]
    risks = [
        "Ledger imports above 50k rows time out; the retry path is untested.",
        "Gateway quota endpoint is a dependency for self-serve onboarding.",
    ]
    alerts = state.record_alerts(needs, risks, {"when": now.isoformat()})
    for entry, record in zip(needs, alerts["needs_you"]):
        entry["alert_id"] = record["id"]
    state.set_alert_status(alerts["risks"][0]["id"], "acknowledged")

    digest = {
        "headline": "Orbit billing sync merged; two reviews and one release note are waiting on you",
        "needs_you": needs,
        "projects": [
            {
                "name": "acme/orbit (billing sync)",
                "status_line": "Nightly reconciliation merged; the staged refactor is up for review",
                "bullets": [
                    "Merged #412: reconcile invoice totals nightly",
                    "Open #418: split the billing sync into testable stages",
                    "Open #405: golden files for the reconciliation report (idle 9 days)",
                ],
            },
            {
                "name": "acme/ledger (imports)",
                "status_line": "Duplicate rows on import still block larger customers",
                "bullets": [
                    "Open #233: drop duplicate entries (review requested)",
                    "Open #219: imports above 50k rows time out (idle 4 days)",
                ],
            },
            {
                "name": "acme/gateway (public API)",
                "status_line": "Rate limiting shipped; quotas are the next dependency",
                "bullets": [
                    "Merged #88: rate-limit the public API",
                    "Open #91: add a per-key quota endpoint (idle 18 days)",
                ],
            },
            {
                "name": "demo-user/pixel-picker",
                "status_line": "Folder memory merged; TestFlight build 3 is blocked on notes",
                "bullets": ["Merged #12: remember the last selected folder", "Open #14: release notes"],
            },
        ],
        "ideas": [
            {
                "idea_id": ideas[0]["id"],
                "title": ideas[0]["title"],
                "area": ideas[0]["area"],
                "why_now": ideas[0]["why_now"],
                "effort": "S",
                "link": "",
            },
            {
                "idea_id": ideas[1]["id"],
                "title": ideas[1]["title"],
                "area": ideas[1]["area"],
                "why_now": ideas[1]["why_now"],
                "effort": "M",
                "link": "",
            },
            {
                "idea_id": ideas[2]["id"],
                "title": ideas[2]["title"],
                "area": ideas[2]["area"],
                "why_now": ideas[2]["why_now"],
                "effort": "M",
                "link": "",
            },
        ],
        "risks": risks,
        "next_actions": [
            "Review acme/orbit #418 and merge if the stage split reads cleanly",
            "Decide on acme/ledger #233: rebase and land, or close with a reason",
            "Write the TestFlight notes for pixel-picker #14",
            "Scope the gateway quota endpoint (#91)",
        ],
    }

    started = now - timedelta(minutes=3)
    run_id = state.start_run("demo", "demo-model", started)
    meta = {
        "when": now,
        "prefix": "Secretary",
        "provider": "demo",
        "model": "demo-model",
        "duration_s": 11.4,
        "counts": {"new": 2, "changed": 3, "open": 10, "commits": 4, "scanned": 10, "alerts": 5},
        "degraded": False,
        "run_id": run_id,
        "dashboard_url": "http://127.0.0.1:8788/",
    }
    paths = render.write_digest(cfg, digest, meta)
    state.finish_run(
        run_id,
        status="ok",
        items_scanned=10,
        items_new=2,
        items_changed=3,
        ideas_new=4,
        digest_path=str(paths["json"]),
        provider="demo",
        model="demo-model",
        finished_at=(started + timedelta(seconds=11.4)).isoformat(),
    )
    previous = now - timedelta(hours=10)
    old_run = state.start_run("demo", "demo-model", previous)
    state.finish_run(
        old_run,
        status="ok",
        items_scanned=9,
        items_new=1,
        items_changed=2,
        ideas_new=1,
        digest_path="",
        provider="demo",
        model="demo-model",
        finished_at=(previous + timedelta(seconds=9.2)).isoformat(),
    )
    state.set_state("last_success_at", now.isoformat())

    state.add_note("Focus on the billing sync until the refactor lands.", source="demo")
    thread = "general"
    state.ensure_thread(thread, "General")
    state.add_message(thread, "user", "What is the shortest path to shipping the billing sync refactor?")
    state.add_message(
        thread,
        "assistant",
        "Land #418 first: it is the only open PR on the sync path and it is already staged. "
        "#405 (golden files) can follow — it protects the report format but does not block the refactor. "
        "Keep #219 (import timeouts) separate; it is a different failure mode.",
    )
    state.add_message(thread, "user", "Remember this idea: emit a per-stage timing metric from the reconciliation job.")
    state.add_message(
        thread,
        "assistant",
        "Noted. Once the staged split merges, per-stage timings will show which step causes the nightly "
        "spike, and the metric is cheap to add now.\n\n— 1 idea tracked.",
    )

    print(f"demo store: {state.root}")
    print(f"demo digest: {paths['json']}")
    print(f"items={len(state.items())} ideas={len(state.ideas())} alerts={len(state.alerts())}")


if __name__ == "__main__":
    main()
