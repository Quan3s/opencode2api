"""Configuration for the OpenCode -> OpenAI bridge (env vars + optional JSON file)."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader; real environment variables always win."""
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def resolve_bun(value: str | None = None) -> str:
    """Resolve the Bun executable, including non-login shells without ~/.bun/bin in PATH."""
    candidate = value or _env("O2A_BUN", "bun")
    if candidate != "bun":
        return candidate  # explicit path stays explicit (including deliberately missing paths)
    found = shutil.which("bun")
    if found:
        return found
    for path in (
        Path.home() / ".bun" / "bin" / "bun",
        Path("/root/.bun/bin/bun"),
        Path("/usr/local/bin/bun"),
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return candidate


def resolve_go(value: str | None = None) -> str:
    """Resolve the Go TLS-transport binary (preferred over Bun in free mode).

    Looks at O2A_GO, a binary bundled in assets/tls_http, then PATH. An empty
    string means "not installed" and lets the bridge fall back to Bun/urllib.
    """
    candidate = value if value is not None else _env("O2A_GO", "")
    if candidate:
        return candidate  # explicit path stays explicit (including deliberately missing paths)
    bundled = Path(__file__).parent / "assets" / "tls_http" / "o2a-tls"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    found = shutil.which("o2a-tls")
    return found or ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8080
    client_api_key: str = ""

    # "opencode": translate to OpenCode's native HTTP API.
    # "openai": forward to an already OpenAI-compatible gateway (opencode2api etc.).
    upstream_mode: str = "opencode"
    opencode_base_url: str = "http://127.0.0.1:4096"
    opencode_api_key: str = ""
    opencode_free_base_url: str = "https://opencode.ai/inference"
    upstream_base_url: str = "https://opencode.ai/inference/openai/v1"
    upstream_api_key: str = ""
    upstream_user_agent: str = "opencode/latest/2.3.0/cli"
    provider_id: str = "opencode"
    default_model: str = "big-pickle"
    model_map: dict[str, str] = field(default_factory=dict)
    append_assistant_cue: bool = True
    stream_chunk_chars: int = 12
    timeout: int = 180
    # Go (utls custom ClientHello) is the preferred TLS transport for opencode_free;
    # Bun and urllib are fallbacks. Python code itself stays stdlib-only.
    go_path: str = ""
    bun_path: str = "bun"

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        _load_dotenv()
        cfg = cls(
            host=_env("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8080),
            client_api_key=_env("CLIENT_API_KEY", ""),
            upstream_mode=_env("UPSTREAM_MODE", "opencode").strip().lower(),
            opencode_base_url=_env("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/"),
            opencode_api_key=_env("OPENCODE_API_KEY", ""),
            opencode_free_base_url=_env("OPENCODE_FREE_BASE_URL", "https://opencode.ai/inference").rstrip("/"),
            upstream_base_url=_env("UPSTREAM_BASE_URL", "https://opencode.ai/inference/openai/v1").rstrip("/"),
            upstream_api_key=_env("UPSTREAM_API_KEY", ""),
            upstream_user_agent=_env("UPSTREAM_USER_AGENT", "opencode/latest/2.3.0/cli"),
            provider_id=_env("OPENCODE_PROVIDER_ID", "opencode"),
            default_model=_env("DEFAULT_MODEL", "big-pickle"),
            append_assistant_cue=_env_bool("APPEND_ASSISTANT_CUE", True),
            stream_chunk_chars=_env_int("STREAM_CHUNK_CHARS", 12),
            timeout=_env_int("REQUEST_TIMEOUT", 180),
            go_path=resolve_go(_env("O2A_GO", "")),
            bun_path=resolve_bun(_env("O2A_BUN", "bun")),
        )

        path = config_path or _env("CONFIG_FILE", "")
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("config file must contain a JSON object")
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)

        raw_map = _env("MODEL_MAP", "")
        if raw_map:
            parsed = json.loads(raw_map)
            if isinstance(parsed, dict):
                cfg.model_map.update({str(k): str(v) for k, v in parsed.items()})
        if cfg.upstream_mode == "opencode_free":
            if not os.environ.get("UPSTREAM_USER_AGENT") and "upstream_user_agent" not in (data if path else {}):
                cfg.upstream_user_agent = "opencode/latest/2.3.0/cli"
            if not cfg.upstream_api_key:
                cfg.upstream_api_key = "public"
        if cfg.upstream_mode not in ("opencode", "openai", "opencode_free"):
            raise ValueError("UPSTREAM_MODE must be 'opencode', 'openai' or 'opencode_free'")
        return cfg

    def upstream_headers(self) -> dict[str, str]:
        key = self.opencode_api_key if self.upstream_mode == "opencode" else self.upstream_api_key
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.upstream_user_agent,
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers
