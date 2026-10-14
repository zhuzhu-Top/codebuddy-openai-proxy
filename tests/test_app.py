from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi.testclient import TestClient

from codebuddy_openai_proxy.app import create_app
from codebuddy_openai_proxy.config import Settings
from codebuddy_openai_proxy.credentials import Credentials


class FakeCredentials:
    def __init__(self) -> None:
        self.force_calls = 0

    def get(self, *, force: bool = False) -> Credentials:
        self.force_calls += int(force)
        return Credentials(
            access_token="upstream-token",
            user_id="user",
            enterprise_id="tenant",
        )

    def diagnose(self) -> dict[str, Any]:
        return {"authenticated": True}


class MockAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content


def sse_response(content: str = "hello") -> httpx.Response:
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "model-1",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "model-1",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200,
        stream=MockAsyncStream(body.encode()),
        headers={"content-type": "text/event-stream"},
    )


def make_client(
    handler: Any,
    *,
    max_request_bytes: int = 1024 * 1024,
) -> tuple[TestClient, FakeCredentials]:
    credentials = FakeCredentials()
    settings = Settings(
        models=("model-1",),
        max_request_bytes=max_request_bytes,
    )
    app = create_app(
        settings,
        credentials,
        transport=httpx.MockTransport(handler),
    )
    return TestClient(app), credentials


def request_body(stream: bool = False) -> dict[str, Any]:
    return {
        "model": "custom:zhuzhu-model-1",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
        "reasoning_effort": "high",
    }


def test_non_stream_completion_is_aggregated() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"models": [{"id": "model-1"}]}},
            )
        body = json.loads(await request.aread())
        assert body["model"] == "model-1"
        assert body["stream"] is True
        assert body["reasoningEffort"] == "high"
        assert request.headers["x-model-id"] == "model-1"
        return sse_response("aggregated")

    client, _ = make_client(handler)
    with client:
        response = client.post(
            "/v1/chat/completions",
            json=request_body(),
        )
    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert response.json()["choices"][0]["message"]["content"] == "aggregated"


def test_stream_completion_is_passed_through() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"models": [{"id": "model-1"}]}},
            )
        return sse_response("streamed")

    client, _ = make_client(handler)
    with (
        client,
        client.stream(
            "POST",
            "/v1/chat/completions",
            json=request_body(stream=True),
        ) as response,
    ):
        text = response.read().decode()
    assert response.status_code == 200
    assert '"content": "streamed"' in text
    assert "data: [DONE]" in text


def test_models_need_no_auth_and_request_size_is_limited() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"models": [{"id": "model-1"}]}},
            )
        return sse_response()

    client, _ = make_client(handler, max_request_bytes=32)
    with client:
        models = client.get("/v1/models")
        model = client.get("/v1/models/zhuzhu-model-1")
        missing = client.get("/v1/models/missing")
        too_large = client.post(
            "/v1/chat/completions",
            content=b"x" * 33,
        )
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "zhuzhu-model-1"
    assert model.status_code == 200
    assert model.json()["id"] == "zhuzhu-model-1"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "model_not_found"
    assert too_large.status_code == 413


def test_401_reloads_credentials_once() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"models": [{"id": "model-1"}]}},
            )
        calls += 1
        if calls == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return sse_response("retried")

    client, credentials = make_client(handler)
    with client:
        response = client.post(
            "/v1/chat/completions",
            json=request_body(),
        )
    assert response.status_code == 200
    assert credentials.force_calls == 1


def test_unsupported_openai_endpoint_uses_openai_error_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return sse_response()

    client, _ = make_client(handler)
    with client:
        response = client.post("/v1/embeddings", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unsupported_endpoint"
