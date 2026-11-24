"""Reconstruct the official OpenCode 2.x free-tier inference request shape.

Based on the wire format used by the OpenCode CLI on the /inference origin:
v2 endpoint /openai/v1/chat/completions, the CLI system prompt and toolset,
forced SSE with usage, opaque client-tool names and descending session ids.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any, Iterable

ASSETS = Path(__file__).parent / "assets"
PROMPT = (ASSETS / "opencode_prompt_v230.txt").read_text(encoding="utf-8")
TOOLS = json.loads((ASSETS / "opencode_tools_v230.json").read_text(encoding="utf-8"))
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
PROMPT_MARKER = "You are an AI agent running in OpenCode, a coding agent harness."
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


def official_prompt(model: str) -> str:
    """Render the CLI 2.3.0 system prompt with this request's dynamic fields."""
    api_model = str(model).removeprefix("opencode/").strip() or "unknown"
    model_name = {
        "big-pickle": "Big Pickle",
    }.get(api_model, api_model.replace("-", " ").title())
    cwd = Path.cwd()
    values = {
        "{{MODEL_NAME}}": model_name,
        "{{MODEL_ID}}": api_model,
        "{{DATE}}": dt.datetime.now(dt.timezone.utc).strftime("%a %b %d %Y"),
        "{{CWD}}": str(cwd),
        "{{IS_GIT}}": "yes" if (cwd / ".git").exists() else "no",
    }
    prompt = PROMPT
    for marker, value in values.items():
        prompt = prompt.replace(marker, value)
    return prompt


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    if isinstance(value, dict):
        return _text(value.get("text", value.get("content", "")))
    return ""


def client_tool_wire_name(name: str, used: set[str]) -> str:
    digest = hashlib.sha256(name.encode()).hexdigest()[:12]
    base = "mcp_" + digest
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while True:
        tag = f"_{suffix}"
        candidate = base[: 64 - len(tag)] + tag
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def official_toolset() -> list[dict[str, Any]]:
    """Return the exact tool catalog captured from the official CLI request."""
    return json.loads(json.dumps(TOOLS, ensure_ascii=False))


def shape_chat_body(payload: dict[str, Any], tool_renames: dict[str, str]) -> dict[str, Any]:
    """Mutate a client Chat Completions body into the free-tier wire body."""
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return payload
    model = str(payload.get("model", "big-pickle"))
    prompt = official_prompt(model)
    kept: list[Any] = []
    instructions: list[str] = []
    already_shaped = False
    for message in messages:
        if not isinstance(message, dict):
            kept.append(message)
            continue
        if message.get("role") not in ("system", "developer"):
            kept.append(message)
            continue
        text = _text(message.get("content")).strip()
        if PROMPT_MARKER in text:
            already_shaped = True
        elif text:
            instructions.append(text)
    if not already_shaped:
        system = prompt + ("\n\n" + "\n".join(instructions) if instructions else "")
        kept.insert(0, {"role": "system", "content": system})
    payload["messages"] = kept

    tools = payload.get("tools")
    shaped_tools: list[Any] = []
    used: set[str] = set()
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                shaped_tools.append(tool)
                continue
            function = tool.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                original = function["name"]
                wire = client_tool_wire_name(original, used)
                tool_renames[wire] = original
                function["name"] = wire
                if isinstance(function.get("description"), str):
                    function["description"] = function["description"].replace(" ", "\u00a0")
            shaped_tools.append(tool)
    shaped_tools.extend(official_toolset())
    payload["tools"] = shaped_tools
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
