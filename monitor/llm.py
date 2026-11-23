"""LLM providers: Anthropic API, OpenAI-compatible endpoints, and the claude CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "key_env": "ANTHROPIC_API_KEY",
        "style": "anthropic",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "key_env": "OPENAI_API_KEY",
        "style": "openai",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "style": "openai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-pro",
        "key_env": "GEMINI_API_KEY",
        "style": "openai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4.5",
        "key_env": "OPENROUTER_API_KEY",
        "style": "openai",
    },
    "claude_cli": {
        "base_url": "",
        "model": "",
        "key_env": "",
        "style": "cli",
    },
}


class LlmError(RuntimeError):
    """Raised when the model call fails."""


def resolve(cfg, provider: str | None = None) -> dict:
    name = (provider or cfg.get("llm.provider", "anthropic")).lower()
    if name not in PROVIDERS:
        raise LlmError(f"unknown llm provider: {name} (choose from {', '.join(PROVIDERS)})")
    spec = dict(PROVIDERS[name])
    if cfg.get("llm.base_url"):
        spec["base_url"] = cfg.get("llm.base_url")
    if cfg.get("llm.model"):
        spec["model"] = cfg.get("llm.model")
    if cfg.get("llm.api_key_env"):
        spec["key_env"] = cfg.get("llm.api_key_env")
    spec["name"] = name
    return spec


def complete(cfg, system: str, user: str, provider: str | None = None) -> str:
    spec = resolve(cfg, provider)
    timeout = int(cfg.get("llm.timeout", 180))
    if spec["style"] == "cli":
        return _claude_cli(cfg, system, user, timeout)
    key = cfg.secret(spec["key_env"])
    if not key:
        raise LlmError(
            f"no API key for {spec['name']}: set {spec['key_env']} in the environment, "
            "~/.config/agent-monitor/env, or the login keychain"
        )
    if spec["style"] == "anthropic":
        return _anthropic(cfg, spec, key, system, user, timeout)
    return _openai(cfg, spec, key, system, user, timeout)


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            message = parsed.get("error", {}).get("message") or parsed.get("message") or body
        except json.JSONDecodeError:
            message = body
        raise LlmError(f"HTTP {exc.code}: {str(message)[:500]}") from exc
    except urllib.error.URLError as exc:
        raise LlmError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LlmError(f"request timed out after {timeout}s") from exc


def _anthropic(cfg, spec: dict, key: str, system: str, user: str, timeout: int) -> str:
    payload = {
        "model": spec["model"],
        "max_tokens": int(cfg.get("llm.max_tokens", 4000)),
        "temperature": float(cfg.get("llm.temperature", 0.2)),
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = _post_json(
        f"{spec['base_url'].rstrip('/')}/v1/messages",
        payload,
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        timeout,
    )
    blocks = [block.get("text", "") for block in data.get("content", []) if isinstance(block, dict)]
    text = "".join(blocks).strip()
    if not text:
        raise LlmError("anthropic returned no text")
    return text


def _openai(cfg, spec: dict, key: str, system: str, user: str, timeout: int) -> str:
    payload = {
        "model": spec["model"],
        "max_tokens": int(cfg.get("llm.max_tokens", 4000)),
        "temperature": float(cfg.get("llm.temperature", 0.2)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post_json(
        f"{spec['base_url'].rstrip('/')}/chat/completions",
        payload,
        {"Authorization": f"Bearer {key}"},
        timeout,
    )
    choices = data.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
    if not text:
        raise LlmError(f"{spec['name']} returned no text")
    return text


def _claude_cli(cfg, system: str, user: str, timeout: int) -> str:
    binary = cfg.get("llm.claude_cli_bin", "npx")
    if not shutil.which(binary):
        binary = "claude"
    if not shutil.which(binary):
        raise LlmError("neither npx nor claude is available for the claude_cli provider")
    args = [binary, *[str(arg) for arg in cfg.get("llm.claude_cli_args") or []]]
    model = cfg.get("llm.claude_cli_model") or cfg.get("llm.model")
    if model:
        args.extend(["--model", str(model)])
    prompt = f"{system}\n\n---\n\n{user}"
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
    except subprocess.TimeoutExpired as exc:
        raise LlmError(f"claude CLI timed out after {timeout + 60}s") from exc
    if proc.returncode != 0:
        raise LlmError((proc.stderr or proc.stdout or "claude CLI failed").strip()[:500])
    text = proc.stdout.strip()
    if not text:
        raise LlmError("claude CLI returned no text")
    return text
