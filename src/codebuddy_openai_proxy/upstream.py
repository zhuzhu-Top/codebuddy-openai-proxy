from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from .config import REASONING_EFFORTS, Settings
from .credentials import CredentialProvider, Credentials


class RequestValidationError(ValueError):
    pass


def prepare_upstream_body(body: dict[str, Any]) -> dict[str, Any]:
    result = {**body, "stream": True}
    _validate_openai_options(result)
    options = result.get("stream_options")
    if not isinstance(options, dict):
        options = {}
    result["stream_options"] = {**options, "include_usage": True}

    effort = result.get("reasoning_effort", result.get("reasoningEffort"))
    if effort is not None:
        if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
            supported = ", ".join(sorted(REASONING_EFFORTS))
            raise RequestValidationError(
                f"reasoning_effort must be one of: {supported}"
            )
        result["reasoning_effort"] = effort
        result["reasoningEffort"] = effort

    tool_choice = result.get("tool_choice")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        tools = result.get("tools")
        if not isinstance(name, str) or not name:
            raise RequestValidationError(
                "tool_choice.function.name must be a non-empty string"
            )
        if not isinstance(tools, list):
            raise RequestValidationError(
                "A function tool_choice requires a tools array"
            )
        selected = [
            tool
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("name") == name
        ]
        if not selected:
            raise RequestValidationError(
                f"tool_choice refers to unknown function: {name}"
            )
        # CodeBuddy rejects OpenAI's named-choice object. Requiring the sole
        # selected tool preserves the same behavior.
        result["tools"] = selected
        result["tool_choice"] = "required"

    messages = result.get("messages")
    if isinstance(messages, list):
        result["messages"] = [_normalize_message(message) for message in messages]
    return result


def _normalize_message(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return message
    # CodeBuddy rejects tool call arguments that are not JSON strings, while
    # several OpenAI clients replay them as decoded objects.
    normalized_calls = []
    for call in tool_calls:
        function = call.get("function") if isinstance(call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(function, dict) and not isinstance(arguments, str):
            encoded = json.dumps(
                {} if arguments is None else arguments, ensure_ascii=False
            )
            call = {**call, "function": {**function, "arguments": encoded}}
        normalized_calls.append(call)
    return {**message, "tool_calls": normalized_calls}


def _validate_openai_options(body: dict[str, Any]) -> None:
    n = body.get("n", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise RequestValidationError("n must be a positive integer")
    if n != 1:
        raise RequestValidationError(
            "CodeBuddy only supports n=1; multiple choices are unavailable"
        )
    if body.get("stop") is not None:
        raise RequestValidationError(
            "CodeBuddy does not reliably support stop sequences"
        )
    if body.get("logprobs") is True or body.get("top_logprobs") is not None:
        raise RequestValidationError("CodeBuddy does not provide log probabilities")
    response_format = body.get("response_format")
    if response_format is not None:
        if not isinstance(response_format, dict):
            raise RequestValidationError("response_format must be an object")
        format_type = response_format.get("type")
        if format_type not in {"text", "json_object"}:
            raise RequestValidationError(
                "response_format.type must be 'text' or 'json_object'"
            )


def build_headers(
    settings: Settings,
    credentials: Credentials,
    model: str,
) -> dict[str, str]:
    request_id = uuid.uuid4().hex
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Requested-With": "XMLHttpRequest",
        "X-Agent-Intent": "craft",
        "X-Model-ID": model,
        "X-Request-ID": request_id,
        "X-Trace-ID": request_id,
        "X-Conversation-ID": request_id,
        "X-Conversation-Request-ID": request_id,
        "X-Conversation-Message-ID": uuid.uuid4().hex,
        "X-Request-Trace-Id": str(uuid.uuid4()),
        "X-IDE-Type": "CodeBuddyIDE",
        "X-IDE-Name": "CodeBuddyIDE",
        "X-IDE-Version": settings.ide_version,
        "X-Product-Version": settings.product_version,
        "X-Product": "SaaS",
        "X-Env-ID": "production",
        "User-Agent": f"CodeBuddyIDE/{settings.ide_version}",
    }
    if credentials.user_id:
        headers["X-User-Id"] = credentials.user_id
    if credentials.enterprise_id:
        headers["X-Enterprise-Id"] = credentials.enterprise_id
        headers["X-Tenant-Id"] = credentials.enterprise_id
    if credentials.domain:
        headers["X-Domain"] = credentials.domain
    if credentials.device_token:
        headers["X-Device-Token"] = credentials.device_token
    return headers


class UpstreamClient:
    def __init__(
        self,
        settings: Settings,
        credentials: CredentialProvider,
        client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.credentials = credentials
        self.client = client

    async def send(
        self,
        body: dict[str, Any],
        *,
        stream: bool,
    ) -> httpx.Response:
        model = body["model"]
        upstream_body = prepare_upstream_body(body)
        response = await self._send_once(
            upstream_body,
            model=model,
            stream=stream,
            force_credentials=False,
        )
        if response.status_code != 401:
            return response
        await response.aclose()
        return await self._send_once(
            upstream_body,
            model=model,
            stream=stream,
            force_credentials=True,
        )

    async def _send_once(
        self,
        body: dict[str, Any],
        *,
        model: str,
        stream: bool,
        force_credentials: bool,
    ) -> httpx.Response:
        credentials = self.credentials.get(force=force_credentials)
        request = self.client.build_request(
            "POST",
            self.settings.upstream,
            headers=build_headers(self.settings, credentials, model),
            json=body,
        )
        return await self.client.send(request, stream=stream)
