"""Local dashboard server."""

from __future__ import annotations

import json
import re
import sys
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import chat, pipeline, render, store

STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "stage": "",
    "message": "",
}

STAGE_LABELS = {
    "start": ("Starting", 5),
    "collect": ("Collecting from GitHub", 20),
    "diff": ("Computing changes", 40),
    "think": ("Writing the briefing", 55),
    "record": ("Recording alerts and ideas", 80),
    "render": ("Rendering the briefing", 88),
    "deliver": ("Sending the email", 94),
    "done": ("Finished", 100),
    "error": ("Failed", 100),
}

LOCK = threading.Lock()
CFG = None


def _log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def _state():
    return store.open_store(CFG)


def _progress(stage: str, message: str) -> None:
    with LOCK:
        STATE["stage"] = stage
        STATE["message"] = message


def _run_status() -> dict:
    stage = STATE.get("stage") or "start"
    label, percent = STAGE_LABELS.get(stage, ("Working", 10))
    elapsed = 0.0
    if STATE.get("started_at"):
        try:
            start = datetime.fromisoformat(STATE["started_at"])
            end = datetime.now(timezone.utc)
            if not STATE["running"] and STATE.get("finished_at"):
                end = datetime.fromisoformat(STATE["finished_at"])
            elapsed = (end - start).total_seconds()
        except ValueError:
            elapsed = 0.0
    return {
        "running": STATE["running"],
        "stage": stage,
        "label": label,
        "percent": percent,
        "message": STATE.get("message") or "",
        "started_at": STATE.get("started_at"),
        "elapsed_s": round(elapsed, 1),
        "error": STATE.get("error"),
        "finished_at": STATE.get("finished_at"),
    }


def _latest() -> tuple[dict | None, dict | None]:
    for run in _state().runs(10):
        if run.get("digest_path"):
            payload = render.load_digest(run["digest_path"])
            if payload:
                return payload, run
    return None, None


def _run_worker(send_email: bool = True) -> None:
    try:
        pipeline.run_once(CFG, send_email=send_email, log=_log, progress=_progress)
        STATE["error"] = None
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        _log(traceback.format_exc())
    finally:
        with LOCK:
            STATE["running"] = False
            STATE["finished_at"] = datetime.now(timezone.utc).isoformat()


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-monitor/0.2"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        _log(f"{self.address_string()} {fmt % args}")

    def _send(self, body: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str, status: int = 200) -> None:
        self._send(text.encode("utf-8"), status, "text/html; charset=utf-8")

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), status, "application/json")

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _drain(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path
        try:
            if path == "/health":
                state = _state()
                runs = state.runs(1)
                return self._json(
                    {
                        "ok": True,
                        "running": STATE["running"],
                        "error": STATE["error"],
                        "last_run": runs[0] if runs else None,
                        "counts": state.counts(),
                        "store": str(state.root),
                    }
                )
            if path == "/api/run":
                return self._json(_run_status())
            if path == "/api/chat":
                query = parse_qs(urlparse(self.path).query)
                thread = (query.get("thread") or ["general"])[0]
                after = int((query.get("after") or ["0"])[0] or 0)
                return self._json({"messages": _state().messages(thread, after=after)})
            if path == "/chat":
                query = parse_qs(urlparse(self.path).query)
                thread = (query.get("thread") or ["general"])[0]
                state = _state()
                counts = state.counts()
                return self._html(
                    render.chat_page(
                        thread,
                        state.messages(thread),
                        chat.thread_label(state, thread),
                        dashboard_title=CFG.get("dashboard.title", "Secretary"),
                        notes=counts["notes"],
                        ideas=counts["ideas"],
                    )
                )
            if path == "/history":
                state = _state()
                return self._html(
                    render.history_page(state.alerts(), state.ideas(), state.runs(20))
                )
            if path == "/ideas":
                return self._redirect("/history#ideas")
            if path == "/":
                payload, run = _latest()
                banner = ""
                if STATE["running"]:
                    status = _run_status()
                    banner = render.run_progress_html(
                        status["label"],
                        status["message"],
                        status["percent"],
                        status["elapsed_s"],
                    )
                elif STATE["error"]:
                    banner = (
                        f'<strong style="color:#b91c1c">Last run failed.</strong> '
                        f'<span class="muted">{render.esc(STATE["error"])}</span>'
                    )
                return self._html(render.latest_page(payload, run, banner=banner))
            return self._html(render.message_page("Not found", "No such page."), 404)
        except Exception:  # noqa: BLE001 - never kill the server
            _log(traceback.format_exc())
            return self._html(
                render.message_page("Error", "Something went wrong; see the server log."), 500
            )

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path
        body = self._drain()
        try:
            if path == "/api/chat":
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    return self._json({"error": "invalid json"}, 400)
                thread = str(payload.get("thread") or "general")
                message = str(payload.get("message") or "").strip()
                if not message:
                    return self._json({"error": "empty message"}, 400)
                thread, assistant_id = chat.submit(CFG, thread, message)
                threading.Thread(
                    target=chat.answer, args=(CFG, thread, assistant_id), daemon=True
                ).start()
                return self._json({"ok": True, "thread": thread, "pending_id": assistant_id})
            if path == "/chat":
                form = parse_qs(body.decode("utf-8", errors="replace"))
                thread = (form.get("thread") or ["general"])[0]
                message = (form.get("message") or [""])[0].strip()
                if message:
                    thread, assistant_id = chat.submit(CFG, thread, message)
                    threading.Thread(
                        target=chat.answer, args=(CFG, thread, assistant_id), daemon=True
                    ).start()
                return self._redirect("/chat?thread=" + quote(thread))
            if path == "/run":
                started = False
                with LOCK:
                    if not STATE["running"]:
                        STATE.update(
                            running=True,
                            started_at=datetime.now(timezone.utc).isoformat(),
                            finished_at=None,
                            error=None,
                            stage="start",
                            message="Starting a run",
                        )
                        started = True
                if started:
                    threading.Thread(target=_run_worker, daemon=True).start()
                return self._redirect("/")
            state = _state()
            match = re.match(r"^/history/alert/([A-Za-z0-9_-]+)/(ack|dismiss|reopen)$", path)
            if match:
                status = {"ack": "acknowledged", "dismiss": "dismissed", "reopen": "open"}[match.group(2)]
                state.set_alert_status(match.group(1), status)
                return self._redirect("/history#alerts")
            match = re.match(r"^/history/idea/([A-Za-z0-9_-]+)/(accept|dismiss|reopen)$", path)
            if match:
                status = {"accept": "accepted", "dismiss": "dismissed", "reopen": "open"}[match.group(2)]
                state.set_idea_status(match.group(1), status)
                return self._redirect("/history#ideas")
            match = re.match(r"^/ideas/([A-Za-z0-9_-]+)/(accept|dismiss)$", path)
            if match:
                status = "accepted" if match.group(2) == "accept" else "dismissed"
                state.set_idea_status(match.group(1), status)
                return self._redirect("/history#ideas")
            return self._html(render.message_page("Not found", "No such action."), 404)
        except Exception:  # noqa: BLE001
            _log(traceback.format_exc())
            return self._html(render.message_page("Error", "See the server log."), 500)


def serve(cfg, port: int | None = None) -> None:
    global CFG
    CFG = cfg
    port = port or int(cfg.get("general.port", 8787))
    bind = str(cfg.get("dashboard.bind", "127.0.0.1"))
    state = _state()
    stale = state.mark_pending_errors("The server restarted before answering. Ask again.")
    if stale:
        _log(f"marked {stale} pending chat message(s) as failed")
    interrupted = state.mark_running_errors("The server restarted during this run.")
    if interrupted:
        _log(f"marked {interrupted} interrupted run(s) as failed")
    httpd = ThreadingHTTPServer((bind, port), Handler)
    _log(f"dashboard listening on http://{bind}:{port} (store: {state.root})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
