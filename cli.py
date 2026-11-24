#!/usr/bin/env python3
"""Small stdlib CLI wrapper for the opencode2openai bridge.

Examples:
  bin/o2a serve --port 8080
  bin/o2a models
  bin/o2a chat "Chỉ trả lời đúng chữ OK."
  bin/o2a chat "2+2?" --tools-file tools.json --tool-result "4"
  bin/o2a config set CLIENT_API_KEY my-secret
  bin/o2a doctor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def save_env(values: dict[str, str]) -> None:
    lines = ["# opencode2openai CLI config"]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def base_url(args: argparse.Namespace) -> str:
    env = load_env()
    return (
        getattr(args, "base", None)
        or os.environ.get("O2A_BASE_URL")
        or env.get("O2A_BASE_URL")
        or "http://127.0.0.1:8080"
    ).rstrip("/")


def api_key(args: argparse.Namespace) -> str:
    env = load_env()
    return (
        getattr(args, "key", None)
        or os.environ.get("CLIENT_API_KEY")
        or env.get("CLIENT_API_KEY")
        or "demo-local-key"
    )


def request(args: argparse.Namespace, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base_url(args) + path,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Authorization": f"Bearer {api_key(args)}",
            "Content-Type": "application/json",
        },
    )
    try:
        return urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(raw)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = raw
        raise SystemExit(f"upstream HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {base_url(args)}: {exc.reason}") from exc


def cmd_serve(args: argparse.Namespace) -> None:
    env = load_env()
    os.environ.setdefault("UPSTREAM_MODE", args.mode or env.get("UPSTREAM_MODE", "opencode_free"))
    if args.key:
        os.environ["CLIENT_API_KEY"] = args.key
    elif env.get("CLIENT_API_KEY"):
        os.environ.setdefault("CLIENT_API_KEY", env["CLIENT_API_KEY"])
    if env.get("O2A_BUN"):
        os.environ.setdefault("O2A_BUN", env["O2A_BUN"])
    os.execv(sys.executable, [sys.executable, str(ROOT / "app.py"), "--port", str(args.port)])


def cmd_models(args: argparse.Namespace) -> None:
    with request(args, "/v1/models") as response:
        data = json.loads(response.read().decode("utf-8"))
    for item in data.get("data", []):
        print(f"{item['id']:<36} {item.get('owned_by', '')}")


def _print_stream(response: Any) -> str:
    text = ""
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            continue
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                print(delta["content"], end="", flush=True)
                text += str(delta["content"])
            for call in delta.get("tool_calls", []):
                print("\n[tool_call] " + json.dumps(call, ensure_ascii=False))
    print()
    return text


def cmd_chat(args: argparse.Namespace) -> None:
    prompt = " ".join(args.prompt)
    messages: list[dict[str, Any]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": prompt})

    tools = None
    if args.tools_file:
        tools = json.loads(Path(args.tools_file).read_text(encoding="utf-8"))

    if args.responses:
        payload: dict[str, Any] = {"model": args.model, "input": prompt}
    else:
        payload = {"model": args.model, "messages": messages, "stream": args.stream}
        if tools is not None:
            payload["tools"] = tools

    with request(args, "/v1/responses" if args.responses else "/v1/chat/completions", payload) as response:
        if args.stream and not args.responses:
            _print_stream(response)
            return
        data = json.loads(response.read().decode("utf-8"))

    if args.responses:
        text = "".join(
            part.get("text", "")
            for item in data.get("output", [])
            for part in item.get("content", [])
            if isinstance(part, dict)
        )
        print(text)
        return

    message = data["choices"][0]["message"]
    print(message.get("content") or "")
    for call in message.get("tool_calls") or []:
        print("[tool_call] " + json.dumps(call, ensure_ascii=False))

    if args.tool_result and message.get("tool_calls"):
        followup = list(messages) + [
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": message["tool_calls"],
            }
        ]
        for call in message["tool_calls"]:
            followup.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": (call.get("function") or {}).get("name", ""),
                    "content": args.tool_result,
                }
            )
        payload = {"model": args.model, "messages": followup, "stream": False}
        if tools is not None:
            payload["tools"] = tools
        with request(args, "/v1/chat/completions", payload) as response:
            data = json.loads(response.read().decode("utf-8"))
        print(data["choices"][0]["message"].get("content") or "")


def cmd_config(args: argparse.Namespace) -> None:
    env = load_env()
    if args.action == "list":
        for key, value in sorted(env.items()):
            masked = "<hidden>" if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET")) else value
            print(f"{key}={masked}")
        return
    if args.action == "get":
        print(env.get(args.key, ""))
        return
    env[args.key] = args.value
    save_env(env)
    print(f"saved {args.key}")


def cmd_doctor(args: argparse.Namespace) -> None:
    bun = os.environ.get("O2A_BUN") or load_env().get("O2A_BUN") or "bun"
    found = shutil.which(bun)
    print(f"bun: {found or 'MISSING (free mode may return 429)'}")
    with request(args, "/health") as response:
        print(f"health: HTTP {response.status} {response.read().decode('utf-8', 'replace')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="o2a", description="opencode2openai CLI wrapper")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the bridge")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--mode")
    serve.add_argument("--key")
    serve.set_defaults(func=cmd_serve)

    models = sub.add_parser("models", help="list models")
    models.set_defaults(func=cmd_models)

    chat = sub.add_parser("chat", help="send a chat completion")
    chat.add_argument("prompt", nargs="+")
    chat.add_argument("--model", default="big-pickle")
    chat.add_argument("--system")
    chat.add_argument("--stream", action="store_true")
    chat.add_argument("--responses", action="store_true", help="call /v1/responses instead")
    chat.add_argument("--tools-file", help="JSON file containing an array of OpenAI tools")
    chat.add_argument("--tool-result", help="send this result back after a tool call")
    chat.set_defaults(func=cmd_chat)

    config = sub.add_parser("config", help="read/write .env")
    config.add_argument("action", choices=["list", "get", "set"])
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.set_defaults(func=cmd_config)

    doctor = sub.add_parser("doctor", help="check bun and bridge health")
    doctor.set_defaults(func=cmd_doctor)

    for command in (models, chat, doctor):
        command.add_argument("--base", help="bridge base URL")
        command.add_argument("--key", help="bridge API key")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
