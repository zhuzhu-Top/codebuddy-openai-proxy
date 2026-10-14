from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any

from .upstream import RequestValidationError


def responses_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    input_items = body.get("input")
    if not isinstance(input_items, list):
        raise RequestValidationError("'input' must be an array")

    messages: list[dict[str, Any]] = []
    pending_calls: list[dict[str, Any]] = []
    codex_request = False

    def flush_calls() -> None:
        if pending_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(pending_calls),
                }
            )
            pending_calls.clear()

    for item in input_items:
        if not isinstance(item, dict):
            raise RequestValidationError("Each input item must be an object")
        item_type = item.get("type", "message")
        if item_type == "message":
            flush_calls()
            role = item.get("role")
            content = _content_text(item.get("content"))
            if role == "developer" and _is_codex_boilerplate(content):
                codex_request = True
                continue
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant"}:
                raise RequestValidationError(f"Unsupported message role: {role}")
            messages.append({"role": role, "content": content})
        elif item_type == "function_call":
            pending_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or _id("call"),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }
            )
        elif item_type == "function_call_output":
            flush_calls()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": _content_text(item.get("output")),
                }
            )
        elif item_type in {"additional_tools", "configuration_update"}:
            continue
        else:
            raise RequestValidationError(
                f"Unsupported Responses input type: {item_type}"
            )
    flush_calls()

    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        if codex_request and tool.get("name") not in {
            "exec_command",
            "write_stdin",
            "view_image",
        }:
            continue
        function = {
            key: tool[key]
            for key in ("name", "description", "parameters", "strict")
            if key in tool
        }
        tools.append({"type": "function", "function": function})

    result: dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": True,
    }
    if tools:
        result["tools"] = tools
    for source, target in (
        ("parallel_tool_calls", "parallel_tool_calls"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_tokens"),
    ):
        if source in body:
            result[target] = body[source]
    tool_choice = body.get("tool_choice")
    if tool_choice in {"auto", "none", "required"}:
        result["tool_choice"] = tool_choice
    reasoning = body.get("reasoning")
    if (
        not tools
        and isinstance(reasoning, dict)
        and isinstance(reasoning.get("effort"), str)
    ):
        result["reasoning_effort"] = reasoning["effort"]
    return result


def chat_to_response(
    completion: dict[str, Any],
    request_body: dict[str, Any],
) -> dict[str, Any]:
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        output.append(
            {
                "id": _id("msg"),
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        )
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        output.append(
            {
                "id": _id("fc"),
                "type": "function_call",
                "status": "completed",
                "name": function.get("name", ""),
                "arguments": function.get("arguments", "{}"),
                "call_id": call.get("id") or _id("call"),
            }
        )

    usage = completion.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(usage.get("completion_tokens", 0))
    return {
        "id": _id("resp"),
        "object": "response",
        "created_at": int(completion.get("created") or time.time()),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": request_body.get("max_output_tokens"),
        "model": request_body.get("model", completion.get("model", "")),
        "output": output,
        "parallel_tool_calls": request_body.get("parallel_tool_calls", True),
        "previous_response_id": request_body.get("previous_response_id"),
        "reasoning": request_body.get("reasoning"),
        "store": bool(request_body.get("store", False)),
        "temperature": request_body.get("temperature"),
        "text": request_body.get("text", {"format": {"type": "text"}}),
        "tool_choice": request_body.get("tool_choice", "auto"),
        "tools": request_body.get("tools", []),
        "top_p": request_body.get("top_p"),
        "truncation": request_body.get("truncation", "disabled"),
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": int(
                usage.get("total_tokens", input_tokens + output_tokens)
            ),
        },
    }


def response_sse(response: dict[str, Any]) -> Iterable[bytes]:
    sequence = 0

    def event(event_type: str, **values: Any) -> bytes:
        nonlocal sequence
        payload = {"type": event_type, "sequence_number": sequence, **values}
        sequence += 1
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_type}\ndata: {data}\n\n".encode()

    created = {**response, "status": "in_progress", "output": []}
    yield event("response.created", response=created)
    for output_index, item in enumerate(response["output"]):
        yield event(
            "response.output_item.added",
            output_index=output_index,
            item={**item, "status": "in_progress"},
        )
        if item["type"] == "message":
            part = item["content"][0]
            yield event(
                "response.content_part.added",
                item_id=item["id"],
                output_index=output_index,
                content_index=0,
                part={"type": "output_text", "text": "", "annotations": []},
            )
            yield event(
                "response.output_text.delta",
                item_id=item["id"],
                output_index=output_index,
                content_index=0,
                delta=part["text"],
            )
            yield event(
                "response.output_text.done",
                item_id=item["id"],
                output_index=output_index,
                content_index=0,
                text=part["text"],
            )
            yield event(
                "response.content_part.done",
                item_id=item["id"],
                output_index=output_index,
                content_index=0,
                part=part,
            )
        elif item["type"] == "function_call":
            yield event(
                "response.function_call_arguments.delta",
                item_id=item["id"],
                output_index=output_index,
                delta=item["arguments"],
            )
            yield event(
                "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=output_index,
                arguments=item["arguments"],
            )
        yield event(
            "response.output_item.done",
            output_index=output_index,
            item=item,
        )
    yield event("response.completed", response=response)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and part.get("type") in {"input_text", "output_text", "text"}
            and isinstance(part.get("text"), str)
        )
    return json.dumps(content, ensure_ascii=False)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _is_codex_boilerplate(content: str) -> bool:
    return content.startswith(
        (
            "You are a coding agent running in the Codex CLI",
            "<skills_instructions>",
        )
    )
