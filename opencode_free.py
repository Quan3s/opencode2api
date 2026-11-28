"""Adapt a client Chat Completions body to the OpenCode free-tier origin.

Only the transport envelope is reconstructed: v2 endpoint, CLI-like headers and
forced SSE with usage. The client's own system prompt and tool definitions are
passed through unchanged -- injecting the CLI prompt/toolset made models confuse
the client's tools with the official ones (e.g. `bash` vs `shell`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from typing import Any, Iterable

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
SESSION_RE = re.compile(r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
TIME_RE = re.compile(r"^[0-9a-f]{9}f[c-f][0-9a-f]$")
BOOT_NONCE = secrets.token_bytes(16)


def _base62(number: int, width: int) -> str:
    chars = ["0"] * width
    for i in range(width - 1, -1, -1):
        number, remainder = divmod(number, 62)
        chars[i] = ALPHABET[remainder]
    return "".join(chars)


def canonical_session(signal: str) -> str:
    """Mint an official-shaped descending session id from an arbitrary seed."""
    if SESSION_RE.match(signal) and TIME_RE.match(signal[4:16]):
        return signal
    digest = hashlib.sha256(b"ses\x00" + BOOT_NONCE + b"\x00" + signal.encode()).digest()
    window_ms = 30 * 24 * 60 * 60 * 1000
    offset = int.from_bytes(digest[:4], "big") % window_ms
    midnight = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    base_ms = int(midnight.timestamp() * 1000) - offset
    inverted = (~((base_ms << 12) | 1)) & 0xFFFFFFFFFFFF
    return "ses_" + inverted.to_bytes(6, "big").hex() + _base62(int.from_bytes(digest[6:16], "big"), 14)


def request_identity(body: dict[str, Any], headers: dict[str, str], scope: str) -> tuple[str, str]:
    """Return (session id, project id) with per-request project freshness."""
    signal = ""
    for key in ("x-opencode-session", "x-opencode-session-id", "x-session-affinity", "x-session-id"):
        signal = headers.get(key, "")
        if signal:
            break
    if not signal:
        messages = body.get("messages", [])
        system = []
        first_user = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role in ("system", "developer"):
                system.append(content)
            elif role == "user" and not first_user:
                first_user = json.dumps(content, ensure_ascii=False)
                break
        signal = json.dumps(system + [first_user], ensure_ascii=False)
    session = canonical_session(f"{scope}\x00{signal}" if scope else signal)
    project = headers.get("x-opencode-project") or secrets.token_hex(20)
    return session, project


def shape_chat_body(payload: dict[str, Any], tool_renames: dict[str, str]) -> dict[str, Any]:
    """Add only the free-tier transport flags; leave messages and tools intact."""
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}
    payload.setdefault("max_completion_tokens", 32000)
    return payload


def ordered_json(payload: dict[str, Any]) -> bytes:
    """Serialize with the key order the 2.x client emits."""
    order = ["model", "messages", "tools", "stream", "stream_options", "max_completion_tokens"]
    pairs: list[str] = []
    emitted: set[str] = set()
    for key in order:
        if key in payload:
            pairs.append(json.dumps(key) + ":" + json.dumps(payload[key], ensure_ascii=False, separators=(",", ":")))
            emitted.add(key)
    for key in sorted(set(payload) - emitted):
        pairs.append(json.dumps(key) + ":" + json.dumps(payload[key], ensure_ascii=False, separators=(",", ":")))
    return ("{" + ",".join(pairs) + "}").encode("utf-8")


def v2_headers(session: str, project: str, key: str, user_agent: str) -> dict[str, str]:
    """Emit the header set/order used by the 2.3.0 CLI inference call."""
    trace_id = hashlib.sha256(session.encode("utf-8")).hexdigest()[:32]
    span_id = secrets.token_hex(8)
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "b3": f"{trace_id}-{span_id}-1-{span_id}",
        "traceparent": f"00-{trace_id}-{span_id}-01",
        "x-opencode-client": "cli",
        "x-opencode-project": project,
        "x-opencode-session": session,
        "x-opencode-session-id": session,
        "x-session-affinity": session,
        "x-session-id": session,
        "Accept": "*/*",
    }


def parse_sse_lines(lines: Iterable[str]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Collapse an upstream SSE stream into text, tool calls and usage."""
    text = ""
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "error":
            detail = event.get("error", {})
            raise RuntimeError(str(detail.get("message", event))[:2000])
        if not isinstance(event, dict):
            continue
        if event.get("usage"):
            usage = event["usage"]
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        if isinstance(delta, dict):
            text += str(delta.get("content") or "")
            for call in delta.get("tool_calls", []) or []:
                if not isinstance(call, dict):
                    continue
                # Upstream streams one call as several deltas (id/name first,
                # arguments in fragments). Merge them by `index` so the JSON
                # response carries one complete tool call per function.
                index = call.get("index")
                if not isinstance(index, int) or index < 0:
                    index = len(tool_calls)
                while len(tool_calls) <= index:
                    tool_calls.append({})
                merged = tool_calls[index]
                if call.get("id"):
                    merged["id"] = call["id"]
                if call.get("type"):
                    merged["type"] = call["type"]
                fn = call.get("function")
                if isinstance(fn, dict):
                    target = merged.setdefault("function", {})
                    if fn.get("name"):
                        target["name"] = fn["name"]
                    if fn.get("arguments") is not None:
                        target["arguments"] = str(target.get("arguments") or "") + str(fn["arguments"])
    return text, tool_calls, usage


def restore_names(text: str, renames: dict[str, str]) -> str:
    for wire, original in renames.items():
        text = text.replace(wire, original)
    return text


def rewrite_sse_line(line: str, model_aliases: dict[str, str], tool_renames: dict[str, str]) -> str:
    if not line.startswith("data:"):
        return line
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return line
    try:
        event = json.loads(data)
    except ValueError:
        return line
    changed = False
    if isinstance(event, dict):
        model = event.get("model")
        if isinstance(model, str) and model in model_aliases:
            event["model"] = model_aliases[model]
            changed = True
        for choice in event.get("choices", []) or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            for key in ("content",):
                if isinstance(delta.get(key), str):
                    updated = restore_names(delta[key], tool_renames)
                    if updated != delta[key]:
                        delta[key] = updated
                        changed = True
            for call in delta.get("tool_calls", []) or []:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                name = fn.get("name")
                if isinstance(name, str) and name in tool_renames:
                    fn["name"] = tool_renames[name]
                    changed = True
    if not changed:
        return line
    return "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
