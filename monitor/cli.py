"""Command line entry point."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as config_mod
from . import chat, deliver, gh, llm, pipeline, render, server, store

LAUNCH_AGENTS = pathlib.Path.home() / "Library" / "LaunchAgents"
DIGEST_LABEL = "local.agent-monitor.digest"
SERVER_LABEL = "local.agent-monitor.server"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

TEMPLATE = """\
# agent-monitor configuration. Secrets live in ~/.config/agent-monitor/env.

[general]
timezone = "Europe/Prague"
run_times = ["08:00", "18:00"]   # two digests a day
lookback_hours = 36              # overlap when diffing against the last run
first_run_lookback_days = 7
max_lookback_days = 14
stale_after_days = 7             # open item untouched this long counts as idle
stale_repo_days = 90             # repos pushed to less recently get lower priority
port = 8787

[identity]
login = "your-github-login"
name = "Your Name"
bio = \"\"\"
A few sentences about the work you do. The briefing uses this to keep ideas in your
domain and to judge what an obvious win looks like.
\"\"\"
domains = ["the systems you own", "the projects you maintain"]

[github]
orgs = []                        # e.g. ["your-company"]
repos = []                       # always-included repositories
own_repos = true                 # every repo under identity.login
own_repo_limit = 200
priority_repos = ["your-github-login/your-main-project"]
max_items = 60
max_pr_details = 15
commit_limit = 100

[llm]
provider = "anthropic"           # anthropic | openai | deepseek | gemini | openrouter | claude_cli
model = "claude-sonnet-4-5"
fallback_provider = "claude_cli" # used when the primary provider fails
max_tokens = 4000
temperature = 0.2
timeout = 180

[email]
enabled = true
to = ["you@example.com"]
from = "Agent Monitor <agent@example.com>"
transport = "sendmail_ssh"       # sendmail_ssh | smtp
ssh_host = "deploy@example.com"

[dashboard]
title = "Secretary"
bind = "127.0.0.1"

# Durable state lives in files, not in a database.
# [paths]
# store_dir = "store"
"""


def _load(args) -> config_mod.Config:
    path = pathlib.Path(args.config).expanduser() if getattr(args, "config", None) else None
    return config_mod.load(path)


def _state(cfg):
    return store.open_store(cfg)


def _log_factory(cfg):
    log_dir = cfg.ensure_dir("paths.log_dir", "logs")
    path = log_dir / "run.log"

    def log(message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {message}"
        print(line, flush=True)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    return log


def cmd_init(cfg, args) -> int:
    target = cfg.source or (cfg.root / "config.toml")
    if target.exists() and not args.force:
        print(f"config already exists: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.ENV_FILE.touch(mode=0o600, exist_ok=True)
    for key in ("paths.data_dir", "paths.store_dir", "paths.digest_dir", "paths.log_dir"):
        cfg.ensure_dir(key, key.split(".")[-1])
    print(f"wrote {target}")
    print(f"secrets file: {config_mod.ENV_FILE}")
    print("next: agent-monitor set-key ANTHROPIC_API_KEY")
    return 0


def cmd_set_key(cfg, args) -> int:
    value = args.value or (sys.stdin.readline().strip() if not sys.stdin.isatty() else "")
    if not value:
        value = getpass.getpass(f"value for {args.name}: ").strip()
    if not value:
        print("no value given", file=sys.stderr)
        return 1
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    if config_mod.ENV_FILE.exists():
        lines = [
            line
            for line in config_mod.ENV_FILE.read_text(encoding="utf-8").splitlines()
            if line.split("=", 1)[0].strip() != args.name
        ]
    lines.append(f"{args.name}={value}")
    config_mod.ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config_mod.ENV_FILE.chmod(0o600)
    print(f"stored {args.name} in {config_mod.ENV_FILE} (mode 600)")
    return 0


def cmd_collect(cfg, args) -> int:
    tz = ZoneInfo(cfg.get("general.timezone", "Europe/Prague"))
    now = datetime.now(tz)
    if args.since_hours:
        since = now - timedelta(hours=args.since_hours)
    else:
        since = gh.compute_since(cfg, _state(cfg).get_state("last_success_at"), now)
    collection = gh.collect(cfg, since, now)
    if args.json:
        print(json.dumps(collection, indent=2, ensure_ascii=False))
        return 0
    print(f"since {since.isoformat(timespec='minutes')}  ({len(collection['repos'])} repos)")
    print(f"commits: {len(collection['commits'])}")
    for commit in collection["commits"][:8]:
        print(f"  {commit['date'][:16]} {commit['repo']}: {commit['message'][:70]}")
    print(f"PRs: {len(collection['prs'])}  issues: {len(collection['issues'])}")
    for item in sorted(collection["prs"], key=lambda i: i["updated_at"] or "", reverse=True)[:8]:
        print(
            f"  [{item['state']:<6}] {item['repo']}#{item['number']} "
            f"({','.join(item['involvement'])}) {item['title'][:60]}"
        )
    for item in sorted(collection["issues"], key=lambda i: i["updated_at"] or "", reverse=True)[:8]:
        print(
            f"  [{item['state']:<6}] {item['repo']}#{item['number']} "
            f"({','.join(item['involvement'])}) {item['title'][:60]}"
        )
    return 0


def cmd_run(cfg, args) -> int:
    log = _log_factory(cfg)
    result = pipeline.run_once(
        cfg,
        send_email=not args.no_email,
        provider=args.provider,
        since_hours=args.since_hours,
        log=log,
    )
    briefing = result["digest"]
    print()
    print(briefing.get("headline", ""))
    print(f"  needs you: {len(briefing.get('needs_you') or [])}")
    print(f"  ideas: {len(briefing.get('ideas') or [])} new ({result['ideas_added']} added, "
          f"{result['ideas_dropped']} repeat(s) suppressed)")
    print(f"  files: {result['paths']['md']}")
    if result["degraded"]:
        print(f"  note: deterministic summary ({result['meta'].get('error_note')})", file=sys.stderr)
    if result["email_error"]:
        print(f"  email error: {result['email_error']}", file=sys.stderr)
    return 0


def cmd_serve(cfg, args) -> int:
    server.serve(cfg, port=args.port)
    return 0


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "✓" if ok else "✗"
    print(f" {mark} {label}" + (f" — {detail}" if detail else ""))
    return ok


def cmd_doctor(cfg, args) -> int:
    failures = 0
    print("agent-monitor doctor")
    failures += not _check(cfg.source is not None, "config file", str(cfg.source or "not found"))

    try:
        login = json.loads(
            subprocess.run(
                ["gh", "api", "user"], capture_output=True, text=True, timeout=20
            ).stdout
        ).get("login", "")
        failures += not _check(login == cfg.get("identity.login"), "gh auth", login or "not logged in")
    except Exception as exc:  # noqa: BLE001
        failures += not _check(False, "gh auth", str(exc))

    try:
        state = _state(cfg)
        counts = state.counts()
        failures += not _check(
            True,
            "store",
            f"{counts['items']} items · {counts['ideas']} ideas · {counts['alerts']} alerts "
            f"· {counts['notes']} notes ({state.root})",
        )
    except Exception as exc:  # noqa: BLE001
        failures += not _check(False, "store", str(exc))

    try:
        spec = llm.resolve(cfg)
        if spec["style"] == "cli":
            binary = cfg.get("llm.claude_cli_bin", "npx")
            failures += not _check(
                bool(shutil.which(binary) or shutil.which("claude")),
                "llm",
                f"{spec['name']} via CLI",
            )
        else:
            key = cfg.secret(spec["key_env"])
            failures += not _check(
                bool(key),
                "llm",
                f"{spec['name']} / {spec['model']} — {'key present' if key else 'key MISSING (' + spec['key_env'] + ')'}",
            )
    except Exception as exc:  # noqa: BLE001
        failures += not _check(False, "llm", str(exc))

    transport = cfg.get("email.transport")
    if transport == "sendmail_ssh":
        host = cfg.get("email.ssh_host")
        ok = False
        detail = f"ssh {host}"
        if host:
            try:
                proc = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new", host, "true"],
                    capture_output=True,
                    timeout=20,
                )
                ok = proc.returncode == 0
                if not ok:
                    detail += f" — {(proc.stderr or b'').decode()[:120]}"
            except Exception as exc:  # noqa: BLE001
                detail += f" — {exc}"
        failures += not _check(ok, "email", detail)
    else:
        failures += not _check(bool(cfg.get("email.smtp_host")), "email", f"smtp {cfg.get('email.smtp_host')}")

    port = cfg.get("general.port", 8787)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            failures += not _check(response.status == 200, "dashboard", f"up on :{port}")
    except Exception:  # noqa: BLE001
        failures += not _check(False, "dashboard", f"not reachable on :{port} (run: agent-monitor serve)")

    uid = os.getuid()
    loaded = []
    for label in (DIGEST_LABEL, SERVER_LABEL):
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True
        )
        loaded.append(proc.returncode == 0)
    failures += not _check(all(loaded), "launchd", f"digest={loaded[0]} server={loaded[1]}")

    print()
    print("all checks passed" if not failures else f"{failures} check(s) need attention")
    return 0 if not failures else 1


def _plist(label: str, program: list[str], workdir: pathlib.Path, *, calendar=None,
           keep_alive=False, run_at_load=False, out: pathlib.Path, err: pathlib.Path) -> str:
    args = "".join(f"<string>{render.esc(arg)}</string>" for arg in program)
    calendar_xml = ""
    if calendar:
        entries = "".join(
            f"<dict><key>Hour</key><integer>{hour}</integer>"
            f"<key>Minute</key><integer>{minute}</integer></dict>"
            for hour, minute in calendar
        )
        calendar_xml = f"<key>StartCalendarInterval</key><array>{entries}</array>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{render.esc(label)}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>WorkingDirectory</key><string>{render.esc(str(workdir))}</string>
<key>EnvironmentVariables</key><dict>
  <key>PATH</key><string>{PATH_ENV}</string>
  <key>PYTHONPATH</key><string>{render.esc(str(workdir))}</string>
</dict>
{calendar_xml}
<key>RunAtLoad</key><{'true' if run_at_load else 'false'}/>
<key>KeepAlive</key><{'true' if keep_alive else 'false'}/>
<key>StandardOutPath</key><string>{render.esc(str(out))}</string>
<key>StandardErrorPath</key><string>{render.esc(str(err))}</string>
</dict></plist>
"""


def _bootout(label: str) -> None:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True
    )



def cmd_install(cfg, args) -> int:
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    log_dir = cfg.ensure_dir("paths.log_dir", "logs")
    python = sys.executable
    times = []
    for value in cfg.get("general.run_times") or ["08:00", "18:00"]:
        hour, minute = str(value).split(":")
        times.append((int(hour), int(minute)))

    digest = _plist(
        DIGEST_LABEL,
        [python, "-m", "monitor.cli", "run"],
        cfg.root,
        calendar=times,
        out=log_dir / "digest.out.log",
        err=log_dir / "digest.err.log",
    )
    srv = _plist(
        SERVER_LABEL,
        [python, "-m", "monitor.cli", "serve"],
        cfg.root,
        keep_alive=True,
        run_at_load=True,
        out=log_dir / "server.out.log",
        err=log_dir / "server.err.log",
    )
    for label, xml in ((DIGEST_LABEL, digest), (SERVER_LABEL, srv)):
        destination = LAUNCH_AGENTS / f"{label}.plist"
        destination.write_text(xml, encoding="utf-8")
        _bootout(label)
        proc = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(destination)],
            capture_output=True,
            text=True,
        )
        status = "loaded" if proc.returncode == 0 else f"error: {proc.stderr.strip()[:160]}"
        print(f"{label}: {status}")

    if not args.no_link:
        target = pathlib.Path("/opt/homebrew/bin/agent-monitor")
        if target.parent.exists() and os.access(target.parent, os.W_OK):
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(cfg.root / "bin" / "agent-monitor")
            print(f"linked {target} -> {cfg.root / 'bin' / 'agent-monitor'}")
    print("dashboard: http://127.0.0.1:%s/" % cfg.get("general.port", 8787))
    return 0


def cmd_uninstall(cfg, args) -> int:
    for label in (DIGEST_LABEL, SERVER_LABEL):
        _bootout(label)
        path = LAUNCH_AGENTS / f"{label}.plist"
        if path.exists():
            path.unlink()
        print(f"removed {label}")
    target = pathlib.Path("/opt/homebrew/bin/agent-monitor")
    if target.is_symlink():
        target.unlink()
        print(f"removed {target}")
    return 0


def cmd_ideas(cfg, args) -> int:
    state = _state(cfg)
    if args.action == "list":
        for entry in state.ideas():
            link = f" {entry['link']}" if entry.get("link") else ""
            print(f"{entry['id']} [{entry['status']:<9}] {entry['effort'] or 'M'} "
                  f"{entry['title']} ({entry.get('area') or '-'}){link}")
        return 0
    if not args.id:
        print("an id is required for accept/dismiss", file=sys.stderr)
        return 1
    status = "accepted" if args.action == "accept" else "dismissed"
    if not state.set_idea_status(args.id, status):
        print(f"unknown idea: {args.id}", file=sys.stderr)
        return 1
    print(f"idea {args.id} -> {status}")
    return 0


def cmd_ask(cfg, args) -> int:
    message = " ".join(args.message).strip()
    result = chat.ask_once(cfg, args.thread, message, log=lambda _message: None)
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print(result["reply"])
    for action in result.get("applied") or []:
        detail = action.get("title") or action.get("text") or ""
        print(f"  [{action['type']}:{action['status']}] {detail}")
    return 0


def cmd_render(cfg, args) -> int:
    """Re-render an existing digest JSON (no model call)."""
    if args.path:
        source = pathlib.Path(args.path).expanduser()
    else:
        runs = [run for run in _state(cfg).runs(10) if run.get("digest_path")]
        if not runs:
            print("no digest yet", file=sys.stderr)
            return 1
        source = pathlib.Path(runs[0]["digest_path"])
    payload = render.load_digest(source)
    if not payload:
        print(f"cannot read a digest from {source}", file=sys.stderr)
        return 1
    paths = render.write_digest(cfg, payload["digest"], payload["meta"])
    print(f"rewrote {paths['md']}")
    print(f"        {paths['html']}")
    return 0


def cmd_test_email(cfg, args) -> int:
    result = deliver.send_test(cfg)
    print(f"sent via {result['transport']}: {result['subject']}")
    return 0


def cmd_status(cfg, args) -> int:
    runs = _state(cfg).runs(5)
    if not runs:
        print("no runs yet")
        return 0
    for run in runs:
        print(
            f"{run['started_at'][:16]}  {run['status']:<7}  new={run['items_new'] or 0} "
            f"changed={run['items_changed'] or 0} ideas={run['ideas_new'] or 0}"
            + (f"  error={run['error'][:80]}" if run.get("error") else "")
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-monitor", description="Personal engineering secretary")
    parser.add_argument("--config", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a starter config.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    set_key = sub.add_parser("set-key", help="store a secret in ~/.config/agent-monitor/env")
    set_key.add_argument("name")
    set_key.add_argument("--value", help="value (otherwise read from stdin or prompt)")
    set_key.set_defaults(func=cmd_set_key)

    collect = sub.add_parser("collect", help="collect GitHub data and print a summary")
    collect.add_argument("--json", action="store_true")
    collect.add_argument("--since-hours", type=float)
    collect.set_defaults(func=cmd_collect)

    run = sub.add_parser("run", help="produce a briefing (and email it)")
    run.add_argument("--no-email", action="store_true")
    run.add_argument("--provider", help="override llm.provider")
    run.add_argument("--since-hours", type=float)
    run.set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="run the local dashboard")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="check config, auth, llm, mail and launchd")
    doctor.set_defaults(func=cmd_doctor)

    install = sub.add_parser("install", help="install launchd jobs (digest + dashboard)")
    install.add_argument("--no-link", action="store_true", help="do not symlink into /opt/homebrew/bin")
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall", help="remove the launchd jobs")
    uninstall.set_defaults(func=cmd_uninstall)

    ideas = sub.add_parser("ideas", help="inspect or update the idea ledger")
    ideas.add_argument("action", choices=["list", "accept", "dismiss"])
    ideas.add_argument("id", nargs="?")
    ideas.set_defaults(func=cmd_ideas)

    ask = sub.add_parser("ask", help="ask the secretary (same chat as the dashboard)")
    ask.add_argument("message", nargs="+")
    ask.add_argument("--thread", default="general", help="general, or item:owner/repo#123")
    ask.set_defaults(func=cmd_ask)

    re_render = sub.add_parser("render", help="re-render a digest (latest by default)")
    re_render.add_argument("path", nargs="?")
    re_render.set_defaults(func=cmd_render)

    test_email = sub.add_parser("test-email", help="send a delivery test")
    test_email.set_defaults(func=cmd_test_email)

    status = sub.add_parser("status", help="show recent runs")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = _load(args)
    except config_mod.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        return args.func(cfg, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level reporting
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
