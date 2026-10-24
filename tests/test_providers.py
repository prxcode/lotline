"""Meterless Runtime provider against a local stand-in."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from llm import providers
from llm.client import call_llm

TOKEN = "test-runtime-token"
seen = []


class FakeRuntime(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        if self.path == "/v1/models":
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"data": [{"id": "meterless/reasoning"}, {"id": "meterless/fast"}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append({"headers": dict(self.headers), "model": body["model"]})
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        content = json.dumps({"brand_aliases": ["HC Farms"]})
        self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}]})


@pytest.fixture
def runtime(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), FakeRuntime)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("METERLESS_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("METERLESS_ENABLED", "true")
    monkeypatch.delenv("METERLESS_MODEL", raising=False)
    providers._cache.update(at=0.0, status=None)
    seen.clear()
    yield server
    server.shutdown()
    providers._cache.update(at=0.0, status=None)


def test_unauthorized_without_token(runtime, monkeypatch):
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.setenv("MODEL_BASE_URL", "https://integrate.api.nvidia.com/v1")
    assert providers.meterless_status(refresh=True)["state"] == "unauthorized"
    assert providers.meterless_provider() is None


def test_placeholder_token_is_rejected(runtime, monkeypatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "rt-local")
    assert providers.meterless_status(refresh=True)["state"] == "unauthorized"


def test_online_runtime_is_the_first_provider(runtime, monkeypatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)
    status = providers.meterless_status(refresh=True)
    assert status["state"] == "online" and status["models"] == ["meterless/reasoning", "meterless/fast"]
    assert providers.providers()[0].name == "Meterless Runtime"

    answer, model = call_llm("triage", "system", "user", sample=False)
    assert answer == {"brand_aliases": ["HC Farms"]}
    assert model == "meterless/reasoning via Meterless Runtime"
    from llm.client import test_connection
    tested = test_connection()[0]
    assert tested["provider"] == "Meterless Runtime" and tested["ok"] and tested["served_by"] == "meterless/reasoning"
    headers = {k.lower(): v for k, v in seen[-1]["headers"].items()}
    assert headers["meterless-app"] == "lotline" and headers["meterless-scope"] == "user"


def test_runtime_down_falls_through(monkeypatch):
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    providers._cache.update(at=0.0, status=None)
    assert providers.meterless_status(refresh=True)["state"] == "offline"
    answer, model = call_llm("triage", "system", "user", payload={"brand": "Hollow Creek Farms"}, sample=False)
    assert model.startswith("rules (Nemotron unavailable") and answer["brand_aliases"]
    providers._cache.update(at=0.0, status=None)
