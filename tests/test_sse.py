from __future__ import annotations

import json

from codebuddy_openai_proxy.sse import aggregate_chat_completion, iter_sse_data


def event(value: dict[str, object]) -> str:
    return f"data: {json.dumps(value)}\r\n\r\n"


def test_iter_sse_data_handles_comments_and_crlf() -> None:
    payload = ": keepalive\r\ndata: first\r\ndata: second\r\n\r\n"
    assert list(iter_sse_data(payload)) == ["first\nsecond"]


def test_aggregate_content_tools_reasoning_and_usage() -> None:
    payload = "".join(
        [
            event(
                {
                    "id": "chatcmpl-1",
                    "model": "model-1",
                    "created": 123,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": "hello ",
                                "reasoning_content": "think ",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": '{"q":',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            event(
                {
                    "id": "chatcmpl-1",
                    "model": "model-1",
                    "created": 123,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "world",
                                "reasoning_content": "done",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"x"}'},
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
                }
            ),
            "data: [DONE]\r\n\r\n",
        ]
    )

    result = aggregate_chat_completion(payload)
    message = result["choices"][0]["message"]
    assert result["object"] == "chat.completion"
    assert message["content"] == "hello world"
    assert message["reasoning_content"] == "think done"
    assert message["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": '{"q":"x"}',
    }
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["usage"]["total_tokens"] == 5
