"""The end-to-end run: collect, diff, think, record, render, deliver."""

from __future__ import annotations

import fcntl
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import deliver, digest as digest_mod, gh, llm, render, store


class PipelineError(RuntimeError):
    """Raised when a run cannot produce a briefing."""


def _acquire_lock(cfg):
    path = cfg.path("paths.store_dir", "store") / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise PipelineError("another run is already in progress") from exc
    return handle


def run_once(cfg, **kwargs):
    """Serialize runs across processes so two digests never race."""
    handle = _acquire_lock(cfg)
    try:
        return _run_once(cfg, **kwargs)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _run_once(
    cfg,
    *,
    send_email: bool = True,
    provider: str | None = None,
    since_hours: float | None = None,
    log=print,
) -> dict:
    started = time.monotonic()
    tz = ZoneInfo(cfg.get("general.timezone", "Europe/Prague"))
    now = datetime.now(tz)
    state = store.open_store(cfg)

    spec = llm.resolve(cfg, provider)
    run_id = state.start_run(spec["name"], spec.get("model", ""), now.astimezone(timezone.utc))

    try:
        if since_hours is not None:
            since = now - timedelta(hours=since_hours)
        else:
            since = gh.compute_since(cfg, state.get_state("last_success_at"), now)

        log(f"collecting GitHub data since {since.isoformat(timespec='minutes')} ...")
        collection = gh.collect(cfg, since, now)
        log(
            f"collected {len(collection['prs'])} PRs, {len(collection['issues'])} issues, "
            f"{len(collection['commits'])} commits across {len(collection['repos'])} repos"
        )

        state.record_commits(collection["commits"], now)
        diff = state.record_items([*collection["prs"], *collection["issues"]], now)
        current_keys = {item["key"] for item in [*collection["prs"], *collection["issues"]]}
        board = [row for row in state.open_items() if row["key"] in current_keys]
        existing_ideas = state.active_ideas()

        context = digest_mod.build_context(
            cfg, collection, diff, board, existing_ideas, now, notes=state.notes(8)
        )
        system, user = digest_mod.build_prompt(context)

        degraded = False
        error_note = ""
        briefing = None
        used_spec = spec
        candidates = [provider or cfg.get("llm.provider")]
        fallback = cfg.get("llm.fallback_provider")
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        for candidate in candidates:
            try:
                candidate_spec = llm.resolve(cfg, candidate)
                log(
                    f"asking {candidate_spec['name']} "
                    f"({candidate_spec.get('model') or 'default'}) for the briefing ..."
                )
                raw = llm.complete(cfg, system, user, provider=candidate)
                briefing = digest_mod.normalize(digest_mod.parse_response(raw))
                if not briefing["headline"]:
                    raise ValueError("empty headline from model")
                used_spec = candidate_spec
                break
            except Exception as exc:  # noqa: BLE001 - try the next provider
                error_note = f"{type(exc).__name__}: {exc}"
                log(f"{candidate} failed: {error_note}")
        if briefing is None:
            degraded = True
            log("all models failed, falling back to a deterministic summary")
            briefing = digest_mod.fallback_digest(context)
        briefing = digest_mod.enforce_links(briefing, context)

        kept, dropped = digest_mod.dedupe_ideas(briefing["ideas"], existing_ideas)
        added = 0
        for idea in kept:
            created, record = state.add_idea(idea, digest_mod.fingerprint(idea), source="digest")
            idea["idea_id"] = record.get("id", "")
            if created:
                added += 1
        for repeat in dropped:
            match = repeat["matches"][0]
            if match.get("id"):
                state.touch_idea(match["id"])
        if dropped:
            log(f"suppressed {len(dropped)} repeated idea(s)")
        briefing["ideas"] = kept

        alerts = state.record_alerts(
            briefing.get("needs_you") or [],
            briefing.get("risks") or [],
            {"when": now.isoformat()},
        )
        by_title = {record["title"]: record for record in alerts["needs_you"]}
        for entry in briefing.get("needs_you") or []:
            record = by_title.get((entry.get("title") or "").strip())
            if record:
                entry["alert_id"] = record["id"]
        for project in briefing.get("projects") or []:
            state.record_topic("project", project.get("name") or "Project", project)
        for text, record in zip(briefing.get("risks") or [], alerts["risks"]):
            state.record_topic("risk", text, {"text": text}, alert_id=record["id"])
        for action in briefing.get("next_actions") or []:
            state.record_topic("action", action, {"text": action})

        counts = {
            "new": len(diff["new"]),
            "changed": len(diff["changed"]),
            "open": len(board),
            "commits": len(collection["commits"]),
            "scanned": len(collection["prs"]) + len(collection["issues"]),
            "alerts": len(alerts["needs_you"]) + len(alerts["risks"]),
        }
        meta = {
            "when": now,
            "prefix": cfg.get("email.subject_prefix") or "Secretary",
            "provider": used_spec["name"],
            "model": used_spec.get("model") or "",
            "duration_s": time.monotonic() - started,
            "counts": counts,
            "degraded": degraded,
            "error_note": error_note,
            "run_id": run_id,
            "dashboard_url": f"http://127.0.0.1:{cfg.get('general.port', 8787)}/",
        }
        paths = render.write_digest(cfg, briefing, meta)

        email_error = ""
        email_subject = ""
        if send_email and cfg.get("email.enabled", True):
            try:
                result = deliver.send_digest(cfg, briefing, meta)
                email_subject = result["subject"]
                log(f"email sent via {result['transport']}: {result['subject']}")
            except Exception as exc:  # noqa: BLE001 - digest is still valid
                email_error = f"{type(exc).__name__}: {exc}"
                log(f"email delivery failed: {email_error}")
        elif not send_email:
            log("email skipped (--no-email)")

        state.finish_run(
            run_id,
            status="ok",
            items_scanned=counts["scanned"],
            items_new=counts["new"],
            items_changed=counts["changed"],
            ideas_new=added,
            digest_path=str(paths["json"]),
            error=email_error,
            provider=used_spec["name"],
            model=used_spec.get("model") or "",
        )
        state.set_state("last_success_at", now.isoformat())
        duration = time.monotonic() - started
        log(f"briefing written to {paths['md']} ({duration:.1f}s)")
        return {
            "run_id": run_id,
            "digest": briefing,
            "meta": meta,
            "paths": {key: str(value) for key, value in paths.items()},
            "counts": counts,
            "ideas_added": added,
            "ideas_dropped": len(dropped),
            "degraded": degraded,
            "email_error": email_error,
            "email_subject": email_subject,
            "duration_s": duration,
        }
    except Exception as exc:
        state.finish_run(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
        raise
