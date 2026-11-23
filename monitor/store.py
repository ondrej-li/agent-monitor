"""Filesystem persistence: items, ideas, alerts, notes, chats, runs and commits.

Everything durable lives in plain JSON/JSONL files under the store directory so it can be
read, grepped, versioned and never lost in an opaque database.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import re
import sqlite3
from datetime import datetime, timezone

ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
SUBDIRS = ("ideas", "alerts", "notes", "runs", "chats")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def _fingerprint(text: str) -> str:
    tokens = sorted(
        {
            token
            for token in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(token) > 2
        }
    )
    return hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()[:12]


def _new_id(stamp: datetime, fingerprint: str, suffix: str = "") -> str:
    base = f"{stamp.strftime('%Y%m%dT%H%M%S')}-{fingerprint[:6]}"
    return f"{base}{('-'+suffix) if suffix else ''}"


def _safe_id(value: str) -> str:
    if not ID_RE.match(value or ""):
        raise ValueError(f"invalid id: {value!r}")
    return value


def _read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(tmp, path)


class Store:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (self.root / name).mkdir(exist_ok=True)

    @contextlib.contextmanager
    def locked(self):
        handle = (self.root / ".lock").open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    # ---------------------------------------------------------------- state
    @property
    def state_path(self) -> pathlib.Path:
        return self.root / "state.json"

    def state(self) -> dict:
        return _read_json(self.state_path, {})

    def get_state(self, key: str, default=None):
        return self.state().get(key, default)

    def set_state(self, key: str, value) -> None:
        with self.locked():
            data = self.state()
            data[key] = value
            _write_json(self.state_path, data)

    # ---------------------------------------------------------------- items
    @property
    def items_path(self) -> pathlib.Path:
        return self.root / "items.json"

    def items(self) -> dict[str, dict]:
        return _read_json(self.items_path, {})

    def item(self, key: str) -> dict | None:
        return self.items().get(key)

    def record_items(self, items: list[dict], now: datetime) -> dict[str, list[dict]]:
        stamp = now.isoformat()
        result: dict[str, list[dict]] = {"new": [], "changed": [], "unchanged": []}
        with self.locked():
            existing = self.items()
            for item in items:
                old = existing.get(item["key"])
                if old is None:
                    result["new"].append(item)
                elif old.get("state") != item["state"] or old.get("updated_at") != item.get("updated_at"):
                    changed = dict(item)
                    changed["previous_state"] = old.get("state")
                    result["changed"].append(changed)
                else:
                    result["unchanged"].append(item)
                existing[item["key"]] = {
                    **item,
                    "first_seen": (old or {}).get("first_seen", stamp),
                    "last_seen": stamp,
                }
            _write_json(self.items_path, existing)
        return result

    def open_items(self) -> list[dict]:
        rows = [row for row in self.items().values() if row.get("state") == "open"]
        rows.sort(key=lambda row: (row.get("priority", 1), -int(row.get("idle_days") or 0)))
        return rows

    # ---------------------------------------------------------------- ideas
    def ideas(self) -> list[dict]:
        records = [
            record
            for record in (_read_json(path, {}) for path in (self.root / "ideas").glob("*.json"))
            if record
        ]
        records.sort(key=lambda row: row.get("created_at", ""), reverse=True)
        return records

    def active_ideas(self) -> list[dict]:
        return [idea for idea in self.ideas() if idea.get("status") in ("open", "accepted")]

    def idea(self, idea_id: str) -> dict | None:
        return _read_json(self.root / "ideas" / f"{_safe_id(idea_id)}.json", None)

    def add_idea(self, idea: dict, fingerprint: str, source: str = "digest") -> tuple[bool, dict]:
        stamp = utcnow()
        with self.locked():
            for record in self.ideas():
                if record.get("fingerprint") == fingerprint:
                    record["last_seen"] = stamp.isoformat()
                    record["seen_count"] = int(record.get("seen_count") or 1) + 1
                    _write_json(self.root / "ideas" / f"{record['id']}.json", record)
                    return False, record
            record = {
                "id": _new_id(stamp, fingerprint),
                "fingerprint": fingerprint,
                "title": idea.get("title", ""),
                "area": idea.get("area", ""),
                "why_now": idea.get("why_now", ""),
                "effort": idea.get("effort", "M"),
                "link": idea.get("link", ""),
                "status": "open",
                "source": source,
                "seen_count": 1,
                "created_at": stamp.isoformat(),
                "last_seen": stamp.isoformat(),
            }
            _write_json(self.root / "ideas" / f"{record['id']}.json", record)
            return True, record

    def touch_idea(self, idea_id: str) -> None:
        record = self.idea(idea_id)
        if not record:
            return
        record["last_seen"] = now_iso()
        record["seen_count"] = int(record.get("seen_count") or 1) + 1
        _write_json(self.root / "ideas" / f"{record['id']}.json", record)

    def set_idea_status(self, idea_id: str, status: str) -> dict | None:
        record = self.idea(idea_id)
        if not record:
            return None
        record["status"] = status
        record["updated_at"] = now_iso()
        _write_json(self.root / "ideas" / f"{record['id']}.json", record)
        return record

    # ---------------------------------------------------------------- alerts
    @property
    def alerts_dir(self) -> pathlib.Path:
        return self.root / "alerts"

    def alerts(self) -> list[dict]:
        records = [
            record
            for record in (_read_json(path, {}) for path in self.alerts_dir.glob("*.json"))
            if record
        ]
        order = {"open": 0, "acknowledged": 1, "dismissed": 2}

        def sort_key(row):
            stamp = 0.0
            if _is_iso(row.get("last_seen")):
                try:
                    stamp = datetime.fromisoformat(row["last_seen"]).timestamp()
                except ValueError:
                    stamp = 0.0
            return (order.get(row.get("status"), 3), -stamp)

        records.sort(key=sort_key)
        return records

    def alert(self, alert_id: str) -> dict | None:
        return _read_json(self.alerts_dir / f"{_safe_id(alert_id)}.json", None)

    def _save_alert(self, record: dict) -> dict:
        _write_json(self.alerts_dir / f"{record['id']}.json", record)
        return record

    def record_alerts(self, needs_you: list[dict], risks: list[str], meta: dict) -> dict[str, list[dict]]:
        stamp = now_iso()
        when = str(meta.get("when", stamp))
        existing = {record.get("fingerprint"): record for record in self.alerts()}
        result: dict[str, list[dict]] = {"needs_you": [], "risks": []}
        with self.locked():
            for entry in needs_you or []:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                fingerprint = _fingerprint("needs_you " + title)
                record = existing.get(fingerprint)
                if record:
                    record["last_seen"] = stamp
                    record["seen_count"] = int(record.get("seen_count") or 1) + 1
                    record["why"] = entry.get("why") or record.get("why", "")
                    record["link"] = entry.get("link") or record.get("link", "")
                    record["digest_when"] = when
                    self._save_alert(record)
                else:
                    record = {
                        "id": _new_id(utcnow(), fingerprint),
                        "fingerprint": fingerprint,
                        "kind": "needs_you",
                        "title": title,
                        "why": entry.get("why") or "",
                        "link": entry.get("link") or "",
                        "status": "open",
                        "seen_count": 1,
                        "first_seen": stamp,
                        "last_seen": stamp,
                        "digest_when": when,
                    }
                    existing[fingerprint] = record
                    self._save_alert(record)
                result["needs_you"].append(record)
            for risk in risks or []:
                title = (risk or "").strip()
                if not title:
                    continue
                fingerprint = _fingerprint("risk " + title)
                record = existing.get(fingerprint)
                if record:
                    record["last_seen"] = stamp
                    record["seen_count"] = int(record.get("seen_count") or 1) + 1
                    record["digest_when"] = when
                    self._save_alert(record)
                else:
                    record = {
                        "id": _new_id(utcnow(), fingerprint),
                        "fingerprint": fingerprint,
                        "kind": "risk",
                        "title": title,
                        "why": "",
                        "link": "",
                        "status": "open",
                        "seen_count": 1,
                        "first_seen": stamp,
                        "last_seen": stamp,
                        "digest_when": when,
                    }
                    existing[fingerprint] = record
                    self._save_alert(record)
                result["risks"].append(record)
        return result

    def set_alert_status(self, alert_id: str, status: str) -> dict | None:
        record = self.alert(alert_id)
        if not record:
            return None
        record["status"] = status
        record["updated_at"] = now_iso()
        return self._save_alert(record)

    # ---------------------------------------------------------------- notes
    def notes(self, limit: int = 10) -> list[dict]:
        records = [
            record
            for record in (_read_json(path, {}) for path in (self.root / "notes").glob("*.json"))
            if record
        ]
        records.sort(key=lambda row: row.get("created_at", ""), reverse=True)
        return records[:limit]

    def add_note(self, text: str, source: str = "chat") -> dict:
        stamp = utcnow()
        record = {
            "id": _new_id(stamp, _fingerprint(text)),
            "text": text,
            "source": source,
            "created_at": stamp.isoformat(),
        }
        _write_json(self.root / "notes" / f"{record['id']}.json", record)
        return record

    # ---------------------------------------------------------------- chats
    @property
    def chats_dir(self) -> pathlib.Path:
        return self.root / "chats"

    @property
    def chat_index_path(self) -> pathlib.Path:
        return self.chats_dir / "index.json"

    def _chat_file(self, thread: str) -> pathlib.Path:
        digest = hashlib.sha1(thread.encode("utf-8")).hexdigest()[:16]
        return self.chats_dir / f"{digest}.jsonl"

    def ensure_thread(self, thread: str, title: str, kind: str = "general", ref: str = "") -> dict:
        with self.locked():
            index = _read_json(self.chat_index_path, {})
            record = index.get(thread)
            if record is None:
                record = {
                    "thread": thread,
                    "file": self._chat_file(thread).name,
                    "title": title,
                    "kind": kind,
                    "ref": ref,
                    "created_at": now_iso(),
                    "last_at": now_iso(),
                }
            else:
                record["title"] = title or record.get("title", "")
                record["kind"] = kind or record.get("kind", "general")
                record["ref"] = ref or record.get("ref", "")
            index[thread] = record
            _write_json(self.chat_index_path, index)
            return record

    def chat_index(self) -> dict:
        return _read_json(self.chat_index_path, {})

    def threads(self) -> list[dict]:
        index = self.chat_index()
        records = list(index.values())
        records.sort(key=lambda row: row.get("last_at", ""), reverse=True)
        return records

    def messages(self, thread: str, after: int = 0, limit: int = 400) -> list[dict]:
        rows = _read_jsonl(self._chat_file(thread))
        rows = [row for row in rows if int(row.get("id") or 0) > after]
        return rows[-limit:]

    def add_message(
        self,
        thread: str,
        role: str,
        content: str,
        status: str = "ok",
        actions: list | None = None,
    ) -> int:
        title = "General" if thread == "general" else thread
        kind, ref = (thread.split(":", 1) + [""])[:2] if ":" in thread else ("general", "")
        self.ensure_thread(thread, title, kind, ref)
        with self.locked():
            path = self._chat_file(thread)
            rows = _read_jsonl(path)
            message_id = max((int(row.get("id") or 0) for row in rows), default=0) + 1
            rows.append(
                {
                    "id": message_id,
                    "role": role,
                    "content": content,
                    "status": status,
                    "actions": actions or [],
                    "created_at": now_iso(),
                }
            )
            _write_jsonl(path, rows)
            self._touch_thread(thread)
            return message_id

    def _touch_thread(self, thread: str) -> None:
        index = _read_json(self.chat_index_path, {})
        if thread in index:
            index[thread]["last_at"] = now_iso()
            _write_json(self.chat_index_path, index)

    def update_message(self, thread: str, message_id: int, **fields) -> None:
        with self.locked():
            path = self._chat_file(thread)
            rows = _read_jsonl(path)
            for row in rows:
                if int(row.get("id") or 0) == int(message_id):
                    for key in ("content", "status", "actions"):
                        if key in fields:
                            row[key] = fields[key]
                    break
            _write_jsonl(path, rows)
            self._touch_thread(thread)

    def mark_pending_errors(self, reason: str) -> int:
        count = 0
        with self.locked():
            for path in self.chats_dir.glob("*.jsonl"):
                rows = _read_jsonl(path)
                touched = False
                for row in rows:
                    if row.get("status") == "pending":
                        row["status"] = "error"
                        row["content"] = reason
                        touched = True
                        count += 1
                if touched:
                    _write_jsonl(path, rows)
        return count

    # ---------------------------------------------------------------- runs
    @property
    def runs_dir(self) -> pathlib.Path:
        return self.root / "runs"

    def runs(self, limit: int = 20) -> list[dict]:
        records = [
            record
            for record in (_read_json(path, {}) for path in self.runs_dir.glob("*.json"))
            if record
        ]
        records.sort(key=lambda row: row.get("started_at", ""), reverse=True)
        return records[:limit]

    def run(self, run_id: str) -> dict | None:
        return _read_json(self.runs_dir / f"{_safe_id(run_id)}.json", None)

    def start_run(self, provider: str, model: str, now: datetime) -> str:
        run_id = now.strftime("%Y%m%dT%H%M%S%f")
        record = {
            "id": run_id,
            "started_at": now.isoformat(),
            "finished_at": None,
            "status": "running",
            "provider": provider,
            "model": model,
            "items_scanned": 0,
            "items_new": 0,
            "items_changed": 0,
            "ideas_new": 0,
            "digest_path": "",
            "error": "",
        }
        _write_json(self.runs_dir / f"{run_id}.json", record)
        return run_id

    def finish_run(self, run_id: str, **fields) -> None:
        record = self.run(run_id) or {"id": run_id}
        for key in (
            "status",
            "items_scanned",
            "items_new",
            "items_changed",
            "ideas_new",
            "digest_path",
            "error",
            "provider",
            "model",
        ):
            if key in fields:
                record[key] = fields[key]
        record["finished_at"] = fields.get("finished_at") or now_iso()
        _write_json(self.runs_dir / f"{run_id}.json", record)

    # ---------------------------------------------------------------- commits
    @property
    def commits_path(self) -> pathlib.Path:
        return self.root / "commits.json"

    def record_commits(self, commits: list[dict], now: datetime) -> None:
        stamp = now.isoformat()
        with self.locked():
            current = {row["sha"]: row for row in _read_json(self.commits_path, []) if row.get("sha")}
            for commit in commits:
                if not commit.get("sha"):
                    continue
                current[commit["sha"]] = {
                    "sha": commit["sha"],
                    "repo": commit.get("repo", ""),
                    "message": commit.get("message", ""),
                    "date": commit.get("date", ""),
                    "url": commit.get("url", ""),
                    "seen_at": stamp,
                }
            rows = sorted(current.values(), key=lambda row: row.get("date", ""), reverse=True)[:500]
            _write_json(self.commits_path, rows)

    def recent_commits(self, limit: int = 40, repo: str | None = None) -> list[dict]:
        rows = _read_json(self.commits_path, [])
        if repo:
            rows = [row for row in rows if row.get("repo") == repo]
        return rows[:limit]

    # ---------------------------------------------------------------- helpers
    def counts(self) -> dict:
        return {
            "items": len(self.items()),
            "ideas": len(self.ideas()),
            "alerts": len(self.alerts()),
            "notes": len(list((self.root / "notes").glob("*.json"))),
            "runs": len(list(self.runs_dir.glob("*.json"))),
            "threads": len(self.chat_index()),
        }


def _is_iso(value) -> bool:
    if not isinstance(value, str) or len(value) < 10:
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def open_store(cfg, *, migrate: bool = True) -> Store:
    root = cfg.path("paths.store_dir", "store")
    store = Store(root)
    if migrate:
        try:
            migrate_sqlite(store, cfg.path("paths.data_dir", "data") / "agent-monitor.db")
        except Exception:  # noqa: BLE001 - a broken legacy DB must not block the app
            pass
    return store


def migrate_sqlite(store: Store, db_path: pathlib.Path) -> bool:
    """One-time import of the old SQLite state, then retire the database file."""
    if not db_path.exists():
        return False
    if store.items() or store.ideas():
        return False
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if "items" in tables:
        items = {}
        for row in con.execute("SELECT * FROM items"):
            record = dict(row)
            record["is_draft"] = bool(record.get("is_draft"))
            items[record["key"]] = record
        if items:
            _write_json(store.items_path, items)

    if "suggestions" in tables:
        for row in con.execute("SELECT * FROM suggestions"):
            record = dict(row)
            created = record.get("created_at") or now_iso()
            try:
                stamp = datetime.fromisoformat(created)
            except ValueError:
                stamp = utcnow()
            record["id"] = _new_id(stamp, _fingerprint(record.get("title", "")))
            record["source"] = "digest"
            record["seen_count"] = 1
            record["last_seen"] = created
            record.pop("first_seen_run", None)
            record.pop("last_seen_run", None)
            _write_json(store.root / "ideas" / f"{record['id']}.json", record)

    if "runs" in tables:
        for row in con.execute("SELECT * FROM runs"):
            record = dict(row)
            started = record.get("started_at") or now_iso()
            try:
                stamp = datetime.fromisoformat(started)
            except ValueError:
                stamp = utcnow()
            record["id"] = stamp.strftime("%Y%m%dT%H%M%S%f")
            record["error"] = record.get("error") or ""
            record["digest_path"] = record.get("digest_path") or ""
            _write_json(store.runs_dir / f"{record['id']}.json", record)

    if "notes" in tables:
        for row in con.execute("SELECT * FROM notes"):
            record = dict(row)
            record["id"] = _new_id(
                datetime.fromisoformat(record["created_at"])
                if _is_iso(record.get("created_at"))
                else utcnow(),
                _fingerprint(record.get("text", "")),
            )
            _write_json(store.root / "notes" / f"{record['id']}.json", record)

    if "commits" in tables:
        rows = [dict(row) for row in con.execute("SELECT * FROM commits ORDER BY date DESC")]
        if rows:
            _write_json(store.commits_path, rows)

    if "chat_messages" in tables:
        index = {}
        for row in con.execute("SELECT * FROM chat_messages ORDER BY id"):
            record = dict(row)
            thread = record["thread"]
            path = store._chat_file(thread)
            rows = _read_jsonl(path)
            rows.append(
                {
                    "id": int(record["id"]),
                    "role": record["role"],
                    "content": record["content"],
                    "status": record.get("status") or "ok",
                    "actions": json.loads(record["actions"]) if record.get("actions") else [],
                    "created_at": record.get("created_at") or now_iso(),
                }
            )
            _write_jsonl(path, rows)
            index.setdefault(
                thread,
                {
                    "thread": thread,
                    "file": path.name,
                    "title": "General" if thread == "general" else thread,
                    "kind": "general" if thread == "general" else thread.split(":", 1)[0],
                    "ref": thread.split(":", 1)[1] if ":" in thread else "",
                    "created_at": record.get("created_at") or now_iso(),
                    "last_at": record.get("created_at") or now_iso(),
                },
            )
        if index:
            _write_json(store.chat_index_path, index)

    state = {}
    if "meta" in tables:
        for row in con.execute("SELECT * FROM meta"):
            state[row["key"]] = row["value"]
    if state:
        _write_json(store.state_path, state)

    con.close()
    db_path.rename(db_path.with_name(db_path.name + ".migrated"))
    return True
