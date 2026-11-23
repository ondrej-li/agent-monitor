"""GitHub data collection through the authenticated gh CLI."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

PR_FIELDS = (
    "repository,number,title,url,state,isDraft,createdAt,updatedAt,author,"
    "assignees,labels,commentsCount"
)
ISSUE_FIELDS = (
    "repository,number,title,url,state,createdAt,updatedAt,author,"
    "assignees,labels,commentsCount"
)
COMMIT_JQ = (
    r'[.items[] | {repo: .repository.full_name, sha: .sha, '
    r'message: (.commit.message | split("\n")[0]), '
    r'date: .commit.committer.date, url: .html_url}]'
)


class GhError(RuntimeError):
    """Raised when a gh invocation fails."""


def _run(args: list[str], timeout: int = 120) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise GhError("gh CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh {' '.join(args[:3])} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "unknown error"
        raise GhError(f"gh {' '.join(args[:3])} failed: {message[:300]}")
    return proc.stdout


def _json(args: list[str], timeout: int = 120):
    output = _run(args, timeout)
    return json.loads(output) if output.strip() else []


def repo_name(repository) -> str:
    if isinstance(repository, dict):
        return repository.get("nameWithOwner") or repository.get("fullName") or ""
    return str(repository or "")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _idle_days(updated_at: str | None, now: datetime) -> int:
    parsed = _parse_time(updated_at)
    return int((now - parsed).total_seconds() // 86400) if parsed else 0


def _merge(bucket: dict[str, dict], item: dict, flag: str) -> None:
    existing = bucket.get(item["key"])
    if existing is None:
        item["involvement"] = [flag]
        bucket[item["key"]] = item
        return
    if flag not in existing["involvement"]:
        existing["involvement"].append(flag)


def _pr(row: dict) -> dict:
    repo = repo_name(row.get("repository"))
    return {
        "key": f"{repo}#{row['number']}",
        "repo": repo,
        "kind": "pr",
        "number": int(row["number"]),
        "title": (row.get("title") or "").strip(),
        "url": row.get("url") or f"https://github.com/{repo}/pull/{row['number']}",
        "state": (row.get("state") or "").lower(),
        "is_draft": bool(row.get("isDraft")),
        "created_at": row.get("createdAt"),
        "updated_at": row.get("updatedAt"),
        "author": (row.get("author") or {}).get("login", ""),
        "assignees": [a.get("login") for a in row.get("assignees") or [] if a],
        "labels": [l.get("name") for l in row.get("labels") or [] if l],
        "comments": int(row.get("commentsCount") or 0),
        "review_decision": row.get("reviewDecision") or "",
    }


def _issue(row: dict) -> dict:
    repo = repo_name(row.get("repository"))
    return {
        "key": f"{repo}#{row['number']}",
        "repo": repo,
        "kind": "issue",
        "number": int(row["number"]),
        "title": (row.get("title") or "").strip(),
        "url": row.get("url") or f"https://github.com/{repo}/issues/{row['number']}",
        "state": (row.get("state") or "").lower(),
        "is_draft": False,
        "created_at": row.get("createdAt"),
        "updated_at": row.get("updatedAt"),
        "author": (row.get("author") or {}).get("login", ""),
        "assignees": [a.get("login") for a in row.get("assignees") or [] if a],
        "labels": [l.get("name") for l in row.get("labels") or [] if l],
        "comments": int(row.get("commentsCount") or 0),
        "review_decision": "",
        "state_reason": row.get("stateReason") or "",
    }


def _search_prs(owner: str, extra: list[str]) -> list[dict]:
    return _json(
        ["search", "prs", "--owner", owner, *extra, "--limit", "100", "--json", PR_FIELDS]
    )


def _search_issues(owner: str, extra: list[str]) -> list[dict]:
    return _json(
        ["search", "issues", "--owner", owner, *extra, "--limit", "100", "--json", ISSUE_FIELDS]
    )


def search_commits(login: str, since: datetime, limit: int) -> list[dict]:
    query = f"author:{login} committer-date:>={since.date().isoformat()}"
    output = _run(
        [
            "api",
            "-X",
            "GET",
            "search/commits",
            "-f",
            f"q={query}",
            "-f",
            "sort=committer-date",
            "-f",
            "order=desc",
            "-f",
            f"per_page={limit}",
            "--jq",
            COMMIT_JQ,
        ]
    )
    commits = json.loads(output) if output.strip() else []
    result = []
    for commit in commits:
        message = (commit.get("message") or "").strip()
        if message.startswith(("Merge pull request", "Merge branch", "Merge remote-tracking")):
            continue
        result.append(
            {
                "repo": commit.get("repo", ""),
                "sha": (commit.get("sha") or "")[:10],
                "message": message[:160],
                "date": commit.get("date"),
                "url": commit.get("url", ""),
            }
        )
    return result


def list_repos(cfg, login: str, now: datetime) -> list[dict]:
    repos: dict[str, dict] = {}
    if cfg.get("github.own_repos", True):
        limit = int(cfg.get("github.own_repo_limit", 200))
        for row in _json(
            [
                "repo",
                "list",
                login,
                "--limit",
                str(limit),
                "--json",
                "nameWithOwner,pushedAt,visibility,isArchived,isFork",
            ]
        ):
            repos[row["nameWithOwner"]] = row
    for name in cfg.get("github.repos") or []:
        if name in repos:
            continue
        row = _json(
            ["repo", "view", name, "--json", "nameWithOwner,pushedAt,visibility,isArchived,isFork"]
        )
        repos[name] = row

    priority_repos = set(cfg.get("github.priority_repos") or [])
    stale_days = int(cfg.get("general.stale_repo_days", 90))
    result = []
    for name, row in repos.items():
        pushed_at = row.get("pushedAt")
        idle = _idle_days(pushed_at, now)
        if name in priority_repos:
            priority = 0
        elif idle > stale_days:
            priority = 2
        else:
            priority = 1
        result.append(
            {
                "name": name,
                "pushed_at": pushed_at,
                "visibility": row.get("visibility", ""),
                "archived": bool(row.get("isArchived")),
                "fork": bool(row.get("isFork")),
                "idle_days": idle,
                "priority": priority,
            }
        )
    result.sort(key=lambda repo: (repo["priority"], repo["idle_days"]))
    return result


def _check_summary(rollup) -> dict:
    if not rollup:
        return {"total": 0, "failing": 0, "pending": 0, "state": "none"}
    failing = pending = 0
    for check in rollup:
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        status = (check.get("status") or "").upper()
        if conclusion in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            failing += 1
        elif state in {"FAILURE", "ERROR"}:
            failing += 1
        elif conclusion in {"", "NEUTRAL", "SKIPPED"} and status not in {"COMPLETED"}:
            pending += 1
        elif state in {"PENDING", "EXPECTED"}:
            pending += 1
    overall = "failing" if failing else ("pending" if pending else "passing")
    return {"total": len(rollup), "failing": failing, "pending": pending, "state": overall}


def pr_detail(repo: str, number: int) -> dict | None:
    try:
        row = _json(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "number,state,isDraft,reviewDecision,mergeable,statusCheckRollup,comments,"
                "updatedAt,additions,deletions,changedFiles,headRefName,baseRefName",
            ],
            timeout=60,
        )
    except GhError:
        return None
    return {
        "review_decision": row.get("reviewDecision") or "",
        "mergeable": row.get("mergeable") or "",
        "checks": _check_summary(row.get("statusCheckRollup")),
        "comments": len(row.get("comments") or []),
        "additions": row.get("additions", 0),
        "deletions": row.get("deletions", 0),
        "changed_files": row.get("changedFiles", 0),
        "head": row.get("headRefName", ""),
        "base": row.get("baseRefName", ""),
    }


def collect(cfg, since: datetime, now: datetime) -> dict:
    login = cfg.require("identity.login")
    owners = [login]
    for owner in cfg.get("github.orgs") or []:
        if owner not in owners:
            owners.append(owner)

    open_prs: dict[str, dict] = {}
    closed_prs: dict[str, dict] = {}
    issues: dict[str, dict] = {}
    since_date = since.date().isoformat()

    for owner in owners:
        for row in _search_prs(owner, ["--involves", login, "--state", "open"]):
            _merge(open_prs, _pr(row), "involved")
        for row in _search_prs(owner, ["--involves", login, "--updated", f">={since_date}"]):
            item = _pr(row)
            bucket = open_prs if item["state"] == "open" else closed_prs
            _merge(bucket, item, "involved")
        for row in _search_prs(owner, ["--review-requested", login, "--state", "open"]):
            _merge(open_prs, _pr(row), "review requested")

        for row in _search_issues(owner, ["--involves", login, "--state", "open"]):
            _merge(issues, _issue(row), "involved")
        for row in _search_issues(owner, ["--involves", login, "--updated", f">={since_date}"]):
            item = _issue(row)
            bucket = issues
            _merge(bucket, item, "involved")

    commits = search_commits(login, since, int(cfg.get("github.commit_limit", 100)))
    repos = list_repos(cfg, login, now)
    repo_priority = {repo["name"]: repo["priority"] for repo in repos}

    for item in [*open_prs.values(), *closed_prs.values(), *issues.values()]:
        if item["author"] == login and "author" not in item["involvement"]:
            item["involvement"].append("author")
        if login in item["assignees"] and "assignee" not in item["involvement"]:
            item["involvement"].append("assignee")
        item["priority"] = repo_priority.get(item["repo"], 1)
        item["idle_days"] = _idle_days(item["updated_at"], now)
        if item["state"] != "open":
            item["idle_days"] = 0

    detail_targets = [
        item
        for item in open_prs.values()
        if "author" in item["involvement"] or "review requested" in item["involvement"]
    ]
    def _order(item):
        parsed = _parse_time(item.get("updated_at"))
        return (item["priority"], -(parsed.timestamp() if parsed else 0))

    detail_targets.sort(key=_order)
    limit = int(cfg.get("github.max_pr_details", 15))
    details: dict[str, dict] = {}
    if detail_targets:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(pr_detail, item["repo"], item["number"]): item
                for item in detail_targets[:limit]
            }
            for future, item in futures.items():
                try:
                    detail = future.result()
                except Exception:  # noqa: BLE001 - a single PR must not break collection
                    detail = None
                if detail:
                    details[item["key"]] = detail

    return {
        "login": login,
        "window": {"since": since.isoformat(), "now": now.isoformat()},
        "repos": repos,
        "prs": [*open_prs.values(), *closed_prs.values()],
        "issues": list(issues.values()),
        "commits": commits,
        "details": details,
    }


def compute_since(cfg, last_success: str | None, now: datetime) -> datetime:
    lookback = timedelta(hours=int(cfg.get("general.lookback_hours", 36)))
    if last_success:
        parsed = _parse_time(last_success)
        if parsed:
            candidate = parsed - timedelta(hours=6)
            floor = now - timedelta(days=int(cfg.get("general.max_lookback_days", 14)))
            return max(candidate, floor)
    return now - timedelta(days=int(cfg.get("general.first_run_lookback_days", 7)))


def local_now(cfg) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(cfg.get("general.timezone", "Europe/Prague")))


__all__ = [
    "GhError",
    "collect",
    "compute_since",
    "list_repos",
    "local_now",
    "pr_detail",
    "search_commits",
    "timezone",
]
