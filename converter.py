"""Translation helpers: OpenAI request/response shapes <-> OpenCode shapes."""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any, Iterable


def chat_completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def response_id() -> str:
    return "resp_" + uuid.uuid4().hex


def estimate_tokens(text: str) -> int:
    """Cheap approximation used only because the OpenCode generate API has no usage."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _content_to_text(content: Any) -> str:
    """Flatten OpenAI message content (string or multimodal parts) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                parts.append(str(part))
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text", "output_text"):
                parts.append(str(part.get("text", "")))
            elif ptype == "image_url":
                url = part.get("image_url", {})
                if isinstance(url, dict):
                    url = url.get("url", "")
                parts.append(f"[image: {url}]")
            elif ptype == "input_audio":
                parts.append("[audio]")
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(p for p in parts if p)
    return str(content)


def messages_to_prompt(messages: Iterable[Any], append_assistant_cue: bool = True) -> str:
    """Turn an OpenAI `messages` array into one prompt for OpenCode generate/text."""
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        text = _content_to_text(message.get("content"))

        if role in ("system", "developer"):
            label = "System"
        elif role == "assistant":
            label = "Assistant"
        elif role == "tool":
            label = "Tool result"
        else:
            label = "User"

        tool_calls = message.get("tool_calls")
        if tool_calls:
            calls = [json.dumps(c, ensure_ascii=False) for c in tool_calls]
            text = (text + "\n" if text else "") + "Tool calls: " + "; ".join(calls)

        if text:
            lines.append(f"{label}: {text}")

    prompt = "\n\n".join(lines)
    if append_assistant_cue and prompt and not prompt.rstrip().endswith("Assistant:"):
        prompt = prompt.rstrip() + "\n\nAssistant:"
    return prompt


def to_opencode_model(model: Any, default_provider: str) -> dict[str, str] | None:
    """Map a requested OpenAI model name to OpenCode's {providerID, id} ref."""
    if not model or model == "auto":
        return None
    name = str(model)
    if "/" in name:
        provider, model_id = name.split("/", 1)
    else:
        provider, model_id = default_provider, name
    return {"providerID": provider, "id": model_id}


def opencode_models_to_openai(rows: Iterable[Any], default_provider: str) -> list[dict[str, Any]]:
    """Map GET /api/model output to GET /v1/models output."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    created = int(time.time())
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id", ""))
        if not model_id:
            continue
        provider = str(row.get("providerID", default_provider))
        ids = [model_id]
        if provider != default_provider:
            ids.insert(0, f"{provider}/{model_id}")
        for identifier in ids:
            if identifier in seen:
                continue
            seen.add(identifier)
            result.append(
                {
                    "id": identifier,
                    "object": "model",
                    "created": created,
                    "owned_by": provider,
                }
            )
    return result


def build_chat_completion(
    text: str,
    model: Any,
    prompt_text: str = "",
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    completion_tokens = estimate_tokens(text)
    prompt_tokens = estimate_tokens(prompt_text)
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    resolved_usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if usage:
        prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", prompt_tokens)))
        completion_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", completion_tokens)))
        resolved_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": usage.get("total_tokens", prompt_tokens + completion_tokens),
        }
    return {
        "id": chat_completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else finish_reason,
            }
        ],
        "usage": resolved_usage,
    }


def stream_chunks(text: str, model: Any, chunk_chars: int = 12) -> Iterable[dict[str, Any]]:
    """Yield OpenAI chat.completion.chunk objects that emulate token streaming."""
    base = {
        "id": chat_completion_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    yield {
        **base,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
    }
    for start in range(0, len(text), max(1, chunk_chars)):
        piece = text[start : start + max(1, chunk_chars)]
        yield {
            **base,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
    yield {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": estimate_tokens(text),
            "total_tokens": estimate_tokens(text),
        },
    }


def responses_input_to_messages(value: Any) -> list[dict[str, Any]]:
    """Map OpenAI Responses `input` to chat messages."""
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            role = item.get("role", "user")
            content = item.get("content", item.get("text", ""))
            if isinstance(content, list):
                content = _content_to_text(content)
            messages.append({"role": role, "content": content})
        else:
            messages.append({"role": "user", "content": str(item)})
    return messages


def build_response_object(text: str, model: Any, prompt_text: str = "") -> dict[str, Any]:
    msg_id = "msg_" + uuid.uuid4().hex
    return {
        "id": response_id(),
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": msg_id,
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
                ],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": estimate_tokens(prompt_text),
            "output_tokens": estimate_tokens(text),
            "total_tokens": estimate_tokens(prompt_text) + estimate_tokens(text),
        },
    }


def rewrite_model_in_json_line(line: str, aliases: dict[str, str]) -> str:
    """Rewrite an SSE `data:` payload's model field back to the client-facing alias."""
    if not aliases:
        return line
    if not line.startswith("data:"):
        return line
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return line
    try:
        obj = json.loads(payload)
    except ValueError:
        return line
    model = obj.get("model") if isinstance(obj, dict) else None
    if isinstance(model, str) and model in aliases:
        obj["model"] = aliases[model]
        return "data: " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    return line
