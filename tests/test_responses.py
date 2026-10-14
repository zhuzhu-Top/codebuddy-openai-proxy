from __future__ import annotations

import json

from codebuddy_openai_proxy.responses import (
    chat_to_response,
    response_sse,
    responses_to_chat,
)


def test_responses_request_converts_messages_tools_and_results() -> None:
    chat = responses_to_chat(
        {
            "model": "zhuzhu-model-1",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Be concise"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "add"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "add",
                    "arguments": '{"a":1,"b":2}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "3",
                },
                {"type": "additional_tools", "tools": []},
                {"type": "configuration_update", "reasoning_effort": "high"},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "add",
                    "description": "Add",
                    "parameters": {"type": "object"},
                },
                {"type": "namespace", "name": "ignored"},
            ],
            "reasoning": {"effort": "high"},
            "max_output_tokens": 123,
        }
    )
    assert chat["messages"][0] == {"role": "system", "content": "Be concise"}
    assert chat["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert chat["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "3",
    }
    assert chat["tools"][0]["function"]["name"] == "add"
    assert len(chat["tools"]) == 1
    assert "reasoning_effort" not in chat
    assert chat["max_tokens"] == 123


def test_reasoning_is_forwarded_when_no_tools_are_present() -> None:
    chat = responses_to_chat(
        {
            "model": "zhuzhu-model-1",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "think"}],
                }
            ],
            "reasoning": {"effort": "high"},
        }
    )
    assert chat["reasoning_effort"] == "high"


def test_codex_boilerplate_is_not_forwarded_upstream() -> None:
    chat = responses_to_chat(
        {
            "model": "zhuzhu-model-1",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are a coding agent running in the Codex CLI, "
                                "a terminal-based coding assistant."
                            ),
                        }
                    ],
                },
                {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "<skills_instructions>large catalog",
                        }
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Keep me"}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {"type": "object"},
                },
                {
                    "type": "function",
                    "name": "request_plugin_install",
                    "parameters": {"type": "object"},
                },
                {"type": "namespace", "name": "ignored"},
            ],
        }
    )
    assert chat["messages"] == [{"role": "user", "content": "Keep me"}]
    assert [tool["function"]["name"] for tool in chat["tools"]] == ["exec_command"]


def test_chat_completion_converts_to_responses_sse() -> None:
    response = chat_to_response(
        {
            "id": "chatcmpl-1",
            "created": 123,
            "model": "real-model",
            "choices": [
                {
                    "message": {
                        "content": "done",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": '{"path":"a"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
        {"model": "zhuzhu-model-1", "stream": True},
    )
    assert response["model"] == "zhuzhu-model-1"
    assert response["output"][0]["content"][0]["text"] == "done"
    assert response["output"][1]["call_id"] == "call_1"
    assert response["usage"]["total_tokens"] == 5

    payload = b"".join(response_sse(response)).decode()
    assert "event: response.output_text.delta" in payload
    assert "event: response.function_call_arguments.done" in payload
    assert "event: response.completed" in payload
    completed = [
        json.loads(line[6:])
        for line in payload.splitlines()
        if line.startswith("data: ")
    ][-1]
    assert completed["response"]["status"] == "completed"
