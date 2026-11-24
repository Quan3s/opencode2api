#!/usr/bin/env python3
"""OpenCode -> OpenAI API bridge.

Exposes OpenAI-shaped endpoints (/v1/chat/completions, /v1/responses, /v1/models)
and translates them to OpenCode's HTTP API, to a reconstructed official free-tier
OpenCode inference request, or to an existing OpenAI-compatible gateway.

Standard library only: python3 app.py. In opencode_free mode a small Bun helper is
used as the TLS transport because the free tier fingerprints the HTTP client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from config import Config
from converter import (
    build_chat_completion,
    build_response_object,
    messages_to_prompt,
    opencode_models_to_openai,
    responses_input_to_messages,
    rewrite_model_in_json_line,
    stream_chunks,
    to_opencode_model,
)
import opencode_free as free


class UpstreamError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload


class BunStream:
    """Minimal HTTPResponse-compatible stream backed by the Bun transport helper."""

    def __init__(self, process: subprocess.Popen[bytes], status: int, headers: dict[str, Any]):
        self.process = process
        self.status = status
        self.headers = headers

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self.process.stdout  # type: ignore[union-attr]
        except GeneratorExit:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            return
        finally:
            try:
                self.process.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
        stderr = self.process.stderr.read() if self.process.stderr else b""  # type: ignore[union-attr]
        self.process.wait(timeout=10)
        if self.process.returncode and stderr:
            raise UpstreamError(502, stderr.decode("utf-8", "replace").strip()[:2000])

    def read(self) -> bytes:
        return b"".join(self.__iter__())


class Bridge:
    def __init__(self, config: Config):
        self.config = config
        self.reverse_aliases = {upstream: alias for alias, upstream in config.model_map.items()}

    def base_url(self) -> str:
        if self.config.upstream_mode == "opencode":
            return self.config.opencode_base_url
        if self.config.upstream_mode == "opencode_free":
            return self.config.opencode_free_base_url
        return self.config.upstream_base_url

    @staticmethod
    def static_response(path: str) -> tuple[bytes, str] | None:
        """Serve the built React dashboard when present; None means no static file."""
        root = (Path(__file__).parent / "dashboard" / "dist").resolve()
        if not root.is_dir():
            return None
        relative = path.lstrip("/")
        if relative in ("", "dashboard", "index.html"):
            candidate = root / "index.html"
        else:
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                return None
        if not candidate.is_file():
            return None
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
            "image/svg+xml",
        ):
            content_type += "; charset=utf-8"
        return candidate.read_bytes(), content_type

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        raw_data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        data = raw_data if raw_data is not None else (
            json.dumps(payload).encode("utf-8") if payload is not None else None
        )
        request = urllib.request.Request(
            self.base_url() + path,
            data=data,
            method=method,
            headers=headers or self.config.upstream_headers(),
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout or self.config.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                message = str(
                    parsed.get("error", {}).get("message")
                    or parsed.get("message")
                    or raw
                )
            except ValueError:
                message = raw
            raise UpstreamError(exc.code, message[:2000], raw) from exc
        except urllib.error.URLError as exc:
            raise UpstreamError(502, f"upstream unavailable: {exc.reason}") from exc

    @staticmethod
    def _error_from_body(status: int, raw: str) -> UpstreamError:
        try:
            parsed = json.loads(raw)
            message = str(parsed.get("error", {}).get("message") or parsed.get("message") or raw)
        except (ValueError, AttributeError):
            message = raw
        return UpstreamError(status, message[:2000], raw)

    def free_request(self, path: str, raw_data: bytes, headers: dict[str, str]) -> Any:
        """POST through Bun so the free tier sees the official CLI TLS fingerprint."""
        helper = Path(__file__).parent / "assets" / "bun_http.js"
        env = os.environ.copy()
        env["O2A_HEADERS_JSON"] = json.dumps(headers)
        try:
            process = subprocess.Popen(
                [self.config.bun_path, str(helper), self.base_url() + path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            # Deployment without Bun: still correct request shape, but the upstream
            # may answer 429 because urllib's TLS fingerprint differs from the CLI.
            return self.request("POST", path, raw_data=raw_data, headers=headers)

        try:
            process.stdin.write(raw_data)  # type: ignore[union-attr]
            process.stdin.close()  # type: ignore[union-attr]
            status_line = process.stdout.readline().decode("utf-8", "replace").strip()  # type: ignore[union-attr]
            header_line = process.stdout.readline().decode("utf-8", "replace").strip()  # type: ignore[union-attr]
            if not status_line.startswith("__O2A_STATUS__ "):
                stderr = process.stderr.read().decode("utf-8", "replace").strip()  # type: ignore[union-attr]
                process.wait(timeout=10)
                raise UpstreamError(502, stderr[:2000] or "Bun transport failed")
            status = int(status_line.split(" ", 1)[1])
            try:
                response_headers = json.loads(header_line.split(" ", 1)[1]) if header_line.startswith("__O2A_HEADERS__ ") else {}
            except ValueError:
                response_headers = {}
            if status >= 400:
                raw = process.stdout.read().decode("utf-8", "replace")  # type: ignore[union-attr]
                process.stderr.close()  # type: ignore[union-attr]
                process.wait(timeout=10)
                raise self._error_from_body(status, raw)
            return BunStream(process, status, response_headers)
        except Exception:
            if process.poll() is None:
                process.kill()
            raise

    def upstream_model(self, requested: Any) -> str:
        name = str(requested or self.config.default_model)
        return str(self.config.model_map.get(name, name))

    def client_model(self, upstream_model: Any) -> Any:
        return self.reverse_aliases.get(str(upstream_model), upstream_model)

    def prepare_free_request(
        self,
        model: Any,
        messages: list[dict[str, Any]],
        raw_body: dict[str, Any] | None,
        client_headers: dict[str, str] | None,
    ) -> tuple[bytes, dict[str, str], dict[str, str]]:
        """Build the byte-for-byte shaped body and v2 headers of the free CLI."""
        payload = dict(raw_body or {})
        payload["model"] = self.upstream_model(model)
        payload["messages"] = messages
        payload.pop("stream_options", None)
        renames: dict[str, str] = {}
        free.shape_chat_body(payload, renames)
        scope = hashlib.sha256(self.config.client_api_key.encode()).hexdigest()[:16] if self.config.client_api_key else ""
        session, project = free.request_identity(payload, client_headers or {}, scope)
        headers = free.v2_headers(
            session,
            project,
            self.config.upstream_api_key or "public",
            self.config.upstream_user_agent,
        )
        return free.ordered_json(payload), headers, renames

    def generate_text(
        self,
        model: Any,
        messages: list[dict[str, Any]],
        raw_body: dict[str, Any] | None = None,
        client_headers: dict[str, str] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Return (assistant text, prompt, metadata) from the configured upstream."""
        upstream_model = self.upstream_model(model)
        prompt = messages_to_prompt(messages, self.config.append_assistant_cue)
        meta: dict[str, Any] = {}

        if self.config.upstream_mode == "opencode":
            payload: dict[str, Any] = {"prompt": prompt}
            ref = to_opencode_model(upstream_model, self.config.provider_id)
            if ref:
                payload["model"] = ref
            response = self.request("POST", "/api/experimental/generate/text", payload)
            try:
                data = json.loads(response.read().decode("utf-8"))
            except ValueError as exc:
                raise UpstreamError(502, "invalid JSON from OpenCode generate endpoint") from exc
            text = (data.get("data") or {}).get("text", "") if isinstance(data, dict) else ""
            return str(text), prompt, meta

        if self.config.upstream_mode == "opencode_free":
            body, headers, renames = self.prepare_free_request(model, messages, raw_body, client_headers)
            meta["tool_renames"] = renames
            response = self.free_request("/openai/v1/chat/completions", body, headers)
            try:
                text, tool_calls, usage = free.parse_sse_lines(
                    line.decode("utf-8", errors="replace") for line in response
                )
            except RuntimeError as exc:
                raise UpstreamError(502, str(exc)) from exc
            meta["tool_calls"] = tool_calls
            meta["usage"] = usage
            return text, prompt, meta

        # OpenAI-compatible upstream (opencode2api, OpenCode inference, etc.)
        response = self.request(
            "POST",
            "/chat/completions",
            {
                "model": upstream_model,
                "messages": messages,
                "stream": False,
            },
        )
        try:
            data = json.loads(response.read().decode("utf-8"))
        except ValueError as exc:
            raise UpstreamError(502, "invalid JSON from upstream chat completions") from exc
        choices = data.get("choices", []) if isinstance(data, dict) else []
        text = ""
        if choices:
            text = str((choices[0].get("message") or {}).get("content") or "")
        return text, prompt, meta


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "opencode2openai/1.0"

    @property
    def bridge(self) -> Bridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- HTTP helpers ----------

    def _cors(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**self._cors(), **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, error_type: str = "invalid_request_error") -> None:
        self._json(status, {"error": {"message": message, "type": error_type, "code": status}})

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _authorized(self) -> bool:
        key = self.bridge.config.client_api_key
        if not key:
            return True
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else header.strip()
        return token == key or self.headers.get("x-api-key", "") == key

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in self._cors().items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    def _sse(self, payload: Any) -> None:
        line = "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    def _sse_raw(self, line: str) -> None:
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    # ---------- Routes ----------

    def do_OPTIONS(self) -> None:  # noqa: N802
        body = b""
        self._send(204, body, "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/health", "/healthz"):
                self._json(
                    200,
                    {
                        "status": "ok",
                        "mode": self.bridge.config.upstream_mode,
                        "upstream": self.bridge.base_url(),
                    },
                )
                return
            if path == "/v1/models":
                if not self._authorized():
                    self._error(401, "invalid API key", "authentication_error")
                    return
                self._models()
                return
            static = self.bridge.static_response("/" if path in ("", "/", "/dashboard") else path)
            if static is not None:
                content, content_type = static
                self._send(200, content, content_type)
                return
            if path == "/":
                self._json(
                    200,
                    {
                        "name": "opencode2openai",
                        "endpoints": ["/v1/models", "/v1/chat/completions", "/v1/responses"],
                        "mode": self.bridge.config.upstream_mode,
                    },
                )
                return
            self._error(404, "not found")
        except UpstreamError as exc:
            self._error(exc.status, exc.message, "upstream_error")
        except Exception as exc:  # pragma: no cover - defensive
            self._error(500, str(exc), "server_error")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in ("/v1/chat/completions", "/v1/responses"):
            self._error(404, "not found")
            return
        if not self._authorized():
            self._error(401, "invalid API key", "authentication_error")
            return
        try:
            body = self._read_json()
            if path == "/v1/chat/completions":
                self._chat_completions(body)
            else:
                self._responses(body)
        except ValueError as exc:
            self._error(400, str(exc))
        except UpstreamError as exc:
            self._error(exc.status, exc.message, "upstream_error")
        except Exception as exc:  # pragma: no cover - defensive
            self._error(500, str(exc), "server_error")

    def _models(self) -> None:
        if self.bridge.config.upstream_mode == "opencode":
            response = self.bridge.request("GET", "/api/model")
            data = json.loads(response.read().decode("utf-8"))
            rows = data.get("data", []) if isinstance(data, dict) else []
            models = opencode_models_to_openai(rows, self.bridge.config.provider_id)
            existing = {item["id"] for item in models}
            for alias in self.bridge.config.model_map:
                if alias not in existing:
                    models.append(
                        {
                            "id": alias,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": self.bridge.config.provider_id,
                        }
                    )
            self._json(200, {"object": "list", "data": models})
            return
        if self.bridge.config.upstream_mode == "opencode_free":
            session, project = free.request_identity({}, {}, "")
            headers = free.v2_headers(
                session,
                project,
                self.bridge.config.upstream_api_key or "public",
                self.bridge.config.upstream_user_agent,
            )
            response = self.bridge.request("GET", "/v1/models", headers=headers)
            self._send(200, response.read(), "application/json; charset=utf-8")
            return
        response = self.bridge.request("GET", "/models")
        self._send(200, response.read(), "application/json; charset=utf-8")

    def _chat_completions(self, body: dict[str, Any]) -> None:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._error(400, "messages must be a non-empty array")
            return
        model = body.get("model") or self.bridge.config.default_model
        if bool(body.get("stream")):
            self._forward_stream(model, messages, body)
            return
        text, prompt, meta = self.bridge.generate_text(
            model, messages, body, dict(self.headers.items())
        )
        tool_calls = meta.get("tool_calls")
        tool_renames = meta.get("tool_renames") or {}
        if tool_calls and tool_renames:
            tool_calls = json.loads(
                free.restore_names(json.dumps(tool_calls, ensure_ascii=False), tool_renames)
            )
        result = build_chat_completion(
            text,
            model,
            prompt,
            usage=meta.get("usage"),
            tool_calls=tool_calls,
        )
        if self.bridge.config.upstream_mode in ("openai", "opencode_free"):
            result["model"] = model
        self._json(200, result)

    def _forward_stream(
        self, model: Any, messages: list[dict[str, Any]], body: dict[str, Any] | None = None
    ) -> None:
        if self.bridge.config.upstream_mode in ("opencode", "opencode_free"):
            if self.bridge.config.upstream_mode == "opencode":
                text, _, _ = self.bridge.generate_text(model, messages)
                aliases: dict[str, str] = {}
                renames: dict[str, str] = {}
            else:
                raw, headers, renames = self.bridge.prepare_free_request(
                    model, messages, body, dict(self.headers.items())
                )
                response = self.bridge.free_request("/openai/v1/chat/completions", raw, headers)
                self._sse_start()
                aliases = self.bridge.reverse_aliases
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace")
                    self._sse_raw(free.rewrite_sse_line(line, aliases, renames))
                self.wfile.flush()
                return
            self._sse_start()
            for chunk in stream_chunks(text, model, self.bridge.config.stream_chunk_chars):
                self._sse(chunk)
            self._sse_raw("data: [DONE]\n\n")
            return

        upstream_model = self.bridge.upstream_model(model)
        response = self.bridge.request(
            "POST",
            "/chat/completions",
            {"model": upstream_model, "messages": messages, "stream": True},
        )
        self._sse_start()
        aliases = self.bridge.reverse_aliases
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace")
            self._sse_raw(rewrite_model_in_json_line(line, aliases))
        self.wfile.flush()

    def _responses(self, body: dict[str, Any]) -> None:
        if bool(body.get("stream")):
            self._error(400, "streaming is supported on /v1/chat/completions only")
            return
        messages = responses_input_to_messages(body.get("input", body.get("prompt", "")))
        if not messages:
            self._error(400, "input must be a string or an array of messages")
            return
        model = body.get("model") or self.bridge.config.default_model
        text, prompt, _ = self.bridge.generate_text(model, messages, None, dict(self.headers.items()))
        self._json(200, build_response_object(text, model, prompt))


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenCode -> OpenAI compatible API bridge")
    parser.add_argument("--config", help="optional JSON config file")
    parser.add_argument("--host", help="override HOST")
    parser.add_argument("--port", type=int, help="override PORT")
    args = parser.parse_args()

    config = Config.load(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    server = ThreadingHTTPServer((config.host, config.port), BridgeHandler)
    server.bridge = Bridge(config)  # type: ignore[attr-defined]
    print(
        f"opencode2openai listening on http://{config.host}:{config.port} "
        f"(mode={config.upstream_mode}, upstream={server.bridge.base_url()})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
