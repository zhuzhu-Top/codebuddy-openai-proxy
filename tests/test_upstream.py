from __future__ import annotations

import pytest

from codebuddy_openai_proxy.config import Settings
from codebuddy_openai_proxy.credentials import Credentials
from codebuddy_openai_proxy.upstream import (
    RequestValidationError,
    build_headers,
    prepare_upstream_body,
)


def test_reasoning_is_normalized_for_codebuddy() -> None:
    body = prepare_upstream_body(
        {
            "model": "model-1",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "reasoning_effort": "high",
        }
    )
    assert body["stream"] is True
    assert body["reasoning_effort"] == "high"
    assert body["reasoningEffort"] == "high"
    assert body["stream_options"]["include_usage"] is True


def test_invalid_reasoning_is_rejected() -> None:
    with pytest.raises(RequestValidationError):
        prepare_upstream_body(
            {
                "model": "model-1",
                "messages": [],
                "reasoning_effort": "extreme",
            }
        )


def test_named_tool_choice_is_normalized_for_codebuddy() -> None:
    body = prepare_upstream_body(
        {
            "model": "model-1",
            "messages": [],
            "tools": [
                {"type": "function", "function": {"name": "first"}},
                {"type": "function", "function": {"name": "selected"}},
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "selected"},
            },
        }
    )
    assert body["tool_choice"] == "required"
    assert body["tools"] == [{"type": "function", "function": {"name": "selected"}}]


def test_object_tool_call_arguments_are_encoded() -> None:
    original = {"a": 17, "label": "加"}
    body = prepare_upstream_body(
        {
            "model": "model-1",
            "messages": [
                {"role": "user", "content": "add"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "add", "arguments": original},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "noop", "arguments": None},
                        },
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {"name": "raw", "arguments": '{"x":1}'},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "42"},
            ],
        }
    )
    calls = body["messages"][1]["tool_calls"]
    assert calls[0]["function"]["arguments"] == '{"a": 17, "label": "加"}'
    assert calls[1]["function"]["arguments"] == "{}"
    assert calls[2]["function"]["arguments"] == '{"x":1}'
    assert body["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "42",
    }


def test_unknown_named_tool_choice_is_rejected() -> None:
    with pytest.raises(RequestValidationError, match="unknown function"):
        prepare_upstream_body(
            {
                "model": "model-1",
                "messages": [],
                "tools": [],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "missing"},
                },
            }
        )


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ({"n": 2}, "only supports n=1"),
        ({"stop": ["END"]}, "stop sequences"),
        ({"logprobs": True}, "log probabilities"),
        (
            {"response_format": {"type": "json_schema"}},
            "response_format.type",
        ),
    ],
)
def test_silently_ignored_upstream_options_are_rejected(
    option: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RequestValidationError, match=message):
        prepare_upstream_body(
            {
                "model": "model-1",
                "messages": [],
                **option,
            }
        )


def test_headers_include_enterprise_identity_without_device_token() -> None:
    headers = build_headers(
        Settings(),
        Credentials(
            access_token="upstream-secret",
            user_id="user-1",
            enterprise_id="tenant-1",
            domain="example.invalid",
        ),
        "model-1",
    )
    assert headers["Authorization"] == "Bearer upstream-secret"
    assert headers["X-User-Id"] == "user-1"
    assert headers["X-Enterprise-Id"] == "tenant-1"
    assert headers["X-Tenant-Id"] == "tenant-1"
    assert "X-Device-Token" not in headers
