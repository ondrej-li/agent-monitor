"""Configuration, paths and secret lookup."""

from __future__ import annotations

import copy
import os
import pathlib
import subprocess
import tomllib
from typing import Any

CONFIG_DIR = pathlib.Path.home() / ".config" / "agent-monitor"
ENV_FILE = CONFIG_DIR / "env"
KEYCHAIN_SERVICE = "agent-monitor"

DEFAULTS: dict[str, Any] = {
    "general": {
        "timezone": "Europe/Prague",
        "run_times": ["08:00", "18:00"],
        "lookback_hours": 36,
        "first_run_lookback_days": 7,
        "max_lookback_days": 14,
        "stale_after_days": 7,
        "stale_repo_days": 90,
        "port": 8787,
    },
    "identity": {
        "login": "",
        "name": "",
        "bio": "",
        "domains": [],
    },
    "github": {
        "orgs": [],
        "repos": [],
        "own_repos": True,
        "own_repo_limit": 200,
        "priority_repos": [],
        "max_items": 60,
        "max_pr_details": 15,
        "commit_limit": 100,
    },
    "llm": {
        "provider": "anthropic",
        "model": "",
        "max_tokens": 4000,
        "temperature": 0.2,
        "timeout": 180,
        "base_url": "",
        "api_key_env": "",
        "fallback_provider": "claude_cli",
        "claude_cli_bin": "npx",
        "claude_cli_args": ["-y", "@anthropic-ai/claude-code@latest", "-p"],
        "claude_cli_model": "",
    },
    "email": {
        "enabled": True,
        "to": [],
        "from": "",
        "transport": "sendmail_ssh",
        "ssh_host": "",
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "",
        "smtp_password_secret": "AGENT_MONITOR_SMTP_PASSWORD",
        "subject_prefix": "Secretary",
    },
    "dashboard": {"title": "Secretary", "bind": "127.0.0.1"},
    "paths": {
        "data_dir": "data",
        "store_dir": "store",
        "digest_dir": "digests",
        "log_dir": "logs",
    },
}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or unusable."""


class SecretError(RuntimeError):
    """Raised when a required secret cannot be found."""


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_env_file(path: pathlib.Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def secret(name: str) -> str:
    """Look up a secret in the environment, then the env file, then the keychain."""
    if not name:
        return ""
    if os.environ.get(name):
        return os.environ[name]
    from_file = read_env_file().get(name)
    if from_file:
        return from_file
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def project_root() -> pathlib.Path:
    override = os.environ.get("AGENT_MONITOR_HOME")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def find_config(root: pathlib.Path | None = None) -> pathlib.Path | None:
    explicit = os.environ.get("AGENT_MONITOR_CONFIG")
    if explicit:
        return pathlib.Path(explicit).expanduser()
    for candidate in ((root or project_root()) / "config.toml", CONFIG_DIR / "config.toml"):
        if candidate.exists():
            return candidate
    return None


class Config:
    def __init__(self, data: dict, root: pathlib.Path, source: pathlib.Path | None = None):
        self.data = data
        self.root = root
        self.source = source

    def get(self, dotted: str, default: Any = None) -> Any:
        current: Any = self.data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return default if current is None else current

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value in (None, "", [], {}):
            raise ConfigError(f"missing required config value: {dotted}")
        return value

    def path(self, dotted: str, default: str | None = None) -> pathlib.Path:
        value = self.get(dotted, default)
        if not value:
            raise ConfigError(f"missing required path config: {dotted}")
        path = pathlib.Path(str(value)).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path

    def ensure_dir(self, dotted: str, default: str | None = None) -> pathlib.Path:
        path = self.path(dotted, default)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def secret(self, name: str) -> str:
        return secret(name)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Config(source={self.source}, root={self.root})"


def load(path: pathlib.Path | None = None) -> Config:
    root = project_root()
    source = path or find_config(root)
    data: dict = {}
    if source is not None:
        if not source.exists():
            raise ConfigError(f"config file not found: {source}")
        with source.open("rb") as handle:
            try:
                data = tomllib.load(handle)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{source}: {exc}") from exc
    return Config(deep_merge(DEFAULTS, data), root, source)
