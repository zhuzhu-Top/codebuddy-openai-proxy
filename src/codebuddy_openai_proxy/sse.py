from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


class SSEParseError(ValueError):
    pass


def iter_sse_data(payload: str) -> Iterable[str]:
    data_lines: list[str] = []
    for raw_line in payload.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if raw_line.startswith(":"):
            continue
        field_name, separator, value = raw_line.partition(":")
        if field_name == "data":
            data_lines.append(
                value[1:] if separator and value.startswith(" ") else value
            )
    if data_lines:
        yield "\n".join(data_lines)


@dataclass
class ChoiceAccumulator:
    content: list[str] = field(default_factory=list)
    reasoning_content: list[str] = field(default_factory=list)
    refusal: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    function_call: dict[str, str] = field(
        default_factory=lambda: {"name": "", "arguments": ""}
    )
    finish_reason: str | None = None
    logprobs: Any = None

    def add(self, choice: dict[str, Any]) -> None:
        delta = choice.get("delta") or {}
        for key, target in (
            ("content", self.content),
            ("reasoning_content", self.reasoning_content),
            ("refusal", self.refusal),
        ):
            value = delta.get(key)
            if isinstance(value, str) and value:
                target.append(value)

        function_call = delta.get("function_call") or {}
        self.function_call["name"] += function_call.get("name") or ""
        self.function_call["arguments"] += function_call.get("arguments") or ""

        for call in delta.get("tool_calls") or []:
            index = int(call.get("index", 0))
            merged = self.tool_calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            merged["id"] = call.get("id") or merged["id"]
            merged["type"] = call.get("type") or merged["type"]
            function = call.get("function") or {}
            merged["function"]["name"] += function.get("name") or ""
            merged["function"]["arguments"] += function.get("arguments") or ""

        self.finish_reason = choice.get("finish_reason") or self.finish_reason
        self.logprobs = (
            choice.get("logprobs")
            if choice.get("logprobs") is not None
            else self.logprobs
        )

    def to_choice(self, index: int) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self.content),
        }
        reasoning = "".join(self.reasoning_content)
        if reasoning:
            message["reasoning_content"] = reasoning
        refusal = "".join(self.refusal)
        if refusal:
            message["refusal"] = refusal
        if self.tool_calls:
            message["tool_calls"] = [
                value for _, value in sorted(self.tool_calls.items())
            ]
        if self.function_call["name"] or self.function_call["arguments"]:
            message["function_call"] = self.function_call
        return {
            "index": index,
            "message": message,
            "logprobs": self.logprobs,
            "finish_reason": self.finish_reason or "stop",
        }


def aggregate_chat_completion(payload: str) -> dict[str, Any]:
    completion_id = ""
    model = ""
    created = int(time.time())
    usage: dict[str, Any] | None = None
    system_fingerprint: str | None = None
    choices: dict[int, ChoiceAccumulator] = {}

    for event in iter_sse_data(payload):
        if not event or event == "[DONE]":
            continue
        try:
            chunk = json.loads(event)
        except json.JSONDecodeError as exc:
            raise SSEParseError("upstream returned malformed SSE JSON") from exc
        if not isinstance(chunk, dict):
            raise SSEParseError("upstream SSE item is not an object")
        completion_id = chunk.get("id") or completion_id
        model = chunk.get("model") or model
        created = chunk.get("created") or created
        usage = chunk.get("usage") or usage
        system_fingerprint = chunk.get("system_fingerprint") or system_fingerprint
        for choice in chunk.get("choices") or []:
            index = int(choice.get("index", 0))
            choices.setdefault(index, ChoiceAccumulator()).add(choice)

    if not choices:
        raise SSEParseError("upstream returned no completion chunks")

    result: dict[str, Any] = {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            accumulator.to_choice(index)
            for index, accumulator in sorted(choices.items())
        ],
    }
    if usage:
        result["usage"] = usage
    if system_fingerprint:
        result["system_fingerprint"] = system_fingerprint
    return result
