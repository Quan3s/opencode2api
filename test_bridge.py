"""Integration tests using a fake upstream. Run: python3 -m unittest -v"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import Bridge, BridgeHandler
from config import Config
import opencode_free


class FakeUpstream(BaseHTTPRequestHandler):
    captured: list[dict] = []
    free_captured: list[dict] = []

    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/model":
            self._reply(200, {"data": [{"id": "big-pickle", "providerID": "opencode"}]})
        elif self.path == "/v1/models":
            self._reply(200, {"object": "list", "data": [{"id": "beam-free"}]})
        else:
            self._reply(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        record = {"path": self.path, "body": body, "auth": self.headers.get("Authorization"), "headers": dict(self.headers)}
        self.captured.append(record)
        if self.path == "/openai/v1/chat/completions":
            self.free_captured.append(record)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.close_connection = True
            events = [
                {"choices": [{"delta": {"content": "Hello "}}]},
                {"choices": [{"delta": {"content": "from free tier"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}},
            ]
            for event in events:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if self.path == "/api/experimental/generate/text":
            self._reply(200, {"data": {"text": "Hello from OpenCode"}})
        elif self.path == "/v1/chat/completions" and body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.close_connection = True
            for word in ("Hello ", "from ", "gateway"):
                self.wfile.write(
                    ("data: " + json.dumps({
                        "id": "chatcmpl-x",
                        "object": "chat.completion.chunk",
                        "model": "beam-free",
                        "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
                    }) + "\n\n").encode()
                )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif self.path == "/v1/chat/completions":
            self._reply(200, {
                "id": "chatcmpl-y",
                "object": "chat.completion",
                "model": "beam-free",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi from gateway"}, "finish_reason": "stop"}],
            })
        else:
            self._reply(404, {"error": {"message": "not found"}})


def _serve(handler, config=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if config is not None:
        server.bridge = Bridge(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _call(url, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        exc.close()
        return exc.code, raw, dict(exc.headers)


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeUpstream.captured = []
        cls.upstream, cls.upstream_url = _serve(FakeUpstream)
        cls.config = Config(
            client_api_key="local-key",
            upstream_mode="opencode",
            opencode_base_url=cls.upstream_url,
            provider_id="opencode",
            default_model="big-pickle",
        )
        cls.server, cls.url = _serve(BridgeHandler, cls.config)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.upstream):
            server.shutdown()
            server.server_close()

    def test_chat_completion_translation(self):
        status, raw, _ = _call(
            self.url,
            "/v1/chat/completions",
            {
                "model": "big-pickle",
                "messages": [
                    {"role": "system", "content": "Be brief"},
                    {"role": "user", "content": "Hello"},
                ],
            },
            {"Authorization": "Bearer local-key"},
        )
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["choices"][0]["message"]["content"], "Hello from OpenCode")
        self.assertEqual(data["model"], "big-pickle")
        self.assertEqual(data["object"], "chat.completion")
        sent = FakeUpstream.captured[-1]["body"]
        self.assertIn("System: Be brief", sent["prompt"])
        self.assertIn("User: Hello", sent["prompt"])
        self.assertTrue(sent["prompt"].rstrip().endswith("Assistant:"))
        self.assertEqual(sent["model"], {"providerID": "opencode", "id": "big-pickle"})

    def test_streaming_sse(self):
        status, raw, headers = _call(
            self.url,
            "/v1/chat/completions",
            {"model": "big-pickle", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
            {"x-api-key": "local-key"},
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        self.assertTrue(raw.endswith("data: [DONE]\n\n"))
        pieces = [
            json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            for line in raw.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        self.assertEqual("".join(pieces), "Hello from OpenCode")

    def test_models_list(self):
        status, raw, _ = _call(self.url, "/v1/models", headers={"Authorization": "Bearer local-key"})
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["object"], "list")
        self.assertEqual(data["data"][0]["id"], "big-pickle")
        self.assertEqual(data["data"][0]["owned_by"], "opencode")

    def test_responses_endpoint(self):
        status, raw, _ = _call(
            self.url,
            "/v1/responses",
            {"model": "big-pickle", "input": "Hello"},
            {"Authorization": "Bearer local-key"},
        )
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["object"], "response")
        self.assertEqual(data["output"][0]["content"][0]["text"], "Hello from OpenCode")

    def test_auth_required(self):
        status, raw, _ = _call(
            self.url, "/v1/chat/completions", {"model": "big-pickle", "messages": [{"role": "user", "content": "Hi"}]}
        )
        self.assertEqual(status, 401)
        self.assertIn("invalid API key", json.loads(raw)["error"]["message"])

    def test_openai_compatible_mode(self):
        config = Config(
            client_api_key="",
            upstream_mode="openai",
            upstream_base_url=self.upstream_url + "/v1",
            upstream_api_key="upstream-key",
            model_map={"gpt-4o": "beam-free"},
        )
        server, url = _serve(BridgeHandler, config)
        try:
            status, raw, _ = _call(
                url,
                "/v1/chat/completions",
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]},
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["choices"][0]["message"]["content"], "Hi from gateway")
            sent = FakeUpstream.captured[-1]
            self.assertEqual(sent["body"]["model"], "beam-free")
            self.assertEqual(sent["auth"], "Bearer upstream-key")

            status, raw, headers = _call(
                url,
                "/v1/chat/completions",
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "stream": True},
            )
            self.assertEqual(status, 200)
            self.assertIn("text/event-stream", headers["Content-Type"])
            self.assertIn('"model":"gpt-4o"', raw)
            self.assertTrue(raw.endswith("data: [DONE]\n\n"))
        finally:
            server.shutdown()
            server.server_close()


class FreeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeUpstream.free_captured = []
        cls.upstream, cls.upstream_url = _serve(FakeUpstream)
        config = Config(
            upstream_mode="opencode_free",
            opencode_free_base_url=cls.upstream_url,
            client_api_key="",
            default_model="big-pickle",
            upstream_api_key="public",
            upstream_user_agent="opencode/latest/2.3.0/cli",
            go_path="",  # keep unit tests hermetic (no real Go/Bun helper)
            bun_path="/nonexistent/bun",
        )
        cls.server, cls.url = _serve(BridgeHandler, config)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_free_request_shape_and_response(self):
        status, raw, _ = _call(
            self.url,
            "/v1/chat/completions",
            {
                "model": "big-pickle",
                "messages": [
                    {"role": "system", "content": "SYSTEM CỦA CLIENT"},
                    {"role": "user", "content": "Hello"},
                ],
                "tools": [{"type": "function", "function": {"name": "my_tool", "description": "x", "parameters": {}}}],
            },
        )
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data["choices"][0]["message"]["content"], "Hello from free tier")
        self.assertEqual(data["usage"]["prompt_tokens"], 9)
        captured = FakeUpstream.free_captured[-1]
        body, headers = captured["body"], {k.lower(): v for k, v in captured["headers"].items()}
        self.assertTrue(body["stream"])
        self.assertTrue(body["stream_options"]["include_usage"])
        self.assertEqual(body["max_completion_tokens"], 32000)
        # No OpenCode CLI prompt/toolset is injected: the client's own messages
        # and tools go through verbatim, so no `bash` vs `shell` confusion.
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYSTEM CỦA CLIENT"})
        self.assertNotIn("You are an AI agent running in OpenCode", json.dumps(body))
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["function"]["name"], "my_tool")
        self.assertEqual(headers["user-agent"], "opencode/latest/2.3.0/cli")
        self.assertEqual(headers["x-opencode-client"], "cli")
        self.assertRegex(headers["b3"], r"^[0-9a-f]{32}-[0-9a-f]{16}-1-[0-9a-f]{16}$")
        self.assertRegex(headers["traceparent"], r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")
        self.assertRegex(headers["x-opencode-session"], r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertRegex(headers["x-opencode-project"], r"^[0-9a-f]{40}$")
        self.assertEqual(captured["auth"], "Bearer public")

    def test_free_stream_passthrough(self):
        status, raw, headers = _call(
            self.url,
            "/v1/chat/completions",
            {"model": "big-pickle", "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        self.assertIn("Hello ", raw)
        self.assertIn("from free tier", raw)
        self.assertTrue(raw.endswith("data: [DONE]\n\n"))


class SseToolCallTests(unittest.TestCase):
    def test_streamed_tool_call_arguments_are_merged(self):
        lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"calc","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"x\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"12}"}}]}}]}',
            'data: {"choices":[{"delta":{"content":"done"}}]}',
            "data: [DONE]",
        ]
        text, calls, _ = opencode_free.parse_sse_lines(lines)
        self.assertEqual(text, "done")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "call_1")
        self.assertEqual(calls[0]["type"], "function")
        self.assertEqual(calls[0]["function"], {"name": "calc", "arguments": '{"x":12}'})


class TransportTests(unittest.TestCase):
    """Verify free_request prefers the configured Go helper without needing a network."""

    def test_go_helper_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "used"
            helper = Path(tmp) / "fake-o2a-tls"
            helper.write_text(
                "#!/bin/sh\n"
                f"echo used > \"$O2A_TEST_MARKER\"\n"
                "echo '__O2A_STATUS__ 200 OK'\n"
                "echo '__O2A_HEADERS__ {\"content-type\":\"text/event-stream\"}'\n"
                "echo 'data: {\"choices\":[{\"delta\":{\"content\":\"from go\"}}]}'\n"
                "echo\n"
                "echo 'data: [DONE]'\n"
                "echo\n",
                encoding="utf-8",
            )
            helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
            os.environ["O2A_TEST_MARKER"] = str(marker)
            try:
                config = Config(
                    upstream_mode="opencode_free",
                    opencode_free_base_url="http://127.0.0.1:1",
                    upstream_api_key="public",
                    go_path=str(helper),
                    bun_path="/nonexistent/bun",
                )
                stream = Bridge(config).free_request(
                    "/openai/v1/chat/completions", b"{}", {"Content-Type": "application/json"}
                )
                self.assertEqual(stream.status, 200)
                self.assertIn(b"from go", stream.read())
                self.assertTrue(marker.is_file(), "the Go helper should have been executed")
            finally:
                os.environ.pop("O2A_TEST_MARKER", None)


if __name__ == "__main__":
    unittest.main()
