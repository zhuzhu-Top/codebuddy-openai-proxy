from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import __version__
from .config import Settings
from .credentials import (
    CredentialError,
    CredentialProvider,
    WindowsCredentialProvider,
)
from .models import ModelCatalog
from .responses import chat_to_response, response_sse, responses_to_chat
from .sse import SSEParseError, aggregate_chat_completion
from .upstream import RequestValidationError, UpstreamClient


def openai_error(
    status_code: int,
    message: str,
    *,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | int | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            }
        },
    )


def create_app(
    settings: Settings | None = None,
    credential_provider: CredentialProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    provider = credential_provider or WindowsCredentialProvider()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(
            resolved_settings.timeout,
            connect=resolved_settings.connect_timeout,
        )
        app.state.http_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=resolved_settings.max_concurrency * 2,
                max_keepalive_connections=resolved_settings.max_concurrency,
            ),
            transport=transport,
        )
        app.state.upstream = UpstreamClient(
            resolved_settings,
            provider,
            app.state.http_client,
        )
        app.state.model_catalog = ModelCatalog(
            resolved_settings,
            provider,
            app.state.http_client,
        )
        app.state.semaphore = asyncio.Semaphore(resolved_settings.max_concurrency)
        yield
        await app.state.http_client.aclose()

    app = FastAPI(
        title="CodeBuddy OpenAI-compatible proxy",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.credential_provider = provider

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> JSONResponse:
        model_ids = await app.state.model_catalog.public_ids()
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": model,
                        "object": "model",
                        "created": 0,
                        "owned_by": "codebuddy",
                    }
                    for model in model_ids
                ],
            }
        )

    @app.get("/v1/models/{model_id:path}")
    async def retrieve_model(model_id: str) -> JSONResponse:
        resolved_model = await app.state.model_catalog.resolve(model_id)
        if resolved_model is None:
            return openai_error(
                404,
                f"The model '{model_id}' does not exist or is not available.",
                param="model",
                code="model_not_found",
            )
        return JSONResponse(
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "codebuddy",
            }
        )

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: Request,
    ) -> JSONResponse | StreamingResponse:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > resolved_settings.max_request_bytes:
                    return openai_error(413, "Request body is too large")
            except ValueError:
                return openai_error(400, "Invalid Content-Length header")
        raw_body = await request.body()
        if len(raw_body) > resolved_settings.max_request_bytes:
            return openai_error(413, "Request body is too large")
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return openai_error(400, "Body must be valid JSON")
        if not isinstance(body, dict):
            return openai_error(400, "Body must be a JSON object")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return openai_error(400, "'model' must be a non-empty string")
        resolved_model = await app.state.model_catalog.resolve(model)
        if resolved_model is None:
            return openai_error(
                404,
                f"The model '{model}' does not exist or is not available.",
                param="model",
                code="model_not_found",
            )
        body = {**body, "model": resolved_model}

        semaphore: asyncio.Semaphore = app.state.semaphore
        await semaphore.acquire()
        response: httpx.Response | None = None
        release_here = True
        try:
            try:
                response = await app.state.upstream.send(
                    body,
                    stream=body.get("stream") is True,
                )
            except CredentialError as exc:
                return openai_error(
                    503,
                    str(exc),
                    error_type="authentication_error",
                )
            except RequestValidationError as exc:
                return openai_error(400, str(exc))
            except httpx.HTTPError as exc:
                return openai_error(
                    502,
                    f"Unable to reach CodeBuddy: {exc}",
                    error_type="api_connection_error",
                )

            if response.status_code >= 400:
                result = await _upstream_error(response)
                response = None
                return result

            if body.get("stream") is True:
                release_here = False
                headers = _forward_headers(response)
                return StreamingResponse(
                    response.aiter_raw(),
                    status_code=response.status_code,
                    headers=headers,
                    media_type="text/event-stream",
                    background=BackgroundTask(
                        _close_stream,
                        response,
                        semaphore,
                    ),
                )

            payload = await response.aread()
            headers = _forward_headers(response)
            await response.aclose()
            response = None
            try:
                completion = aggregate_chat_completion(payload.decode("utf-8"))
            except (SSEParseError, UnicodeDecodeError) as exc:
                return openai_error(
                    502,
                    f"Invalid response from CodeBuddy: {exc}",
                    error_type="api_error",
                )
            return JSONResponse(completion, headers=headers)
        finally:
            if response is not None and release_here:
                await response.aclose()
            if release_here:
                semaphore.release()

    @app.post("/v1/responses", response_model=None)
    async def responses(request: Request) -> JSONResponse | StreamingResponse:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > resolved_settings.max_request_bytes:
                    return openai_error(413, "Request body is too large")
            except ValueError:
                return openai_error(400, "Invalid Content-Length header")
        raw_body = await request.body()
        if len(raw_body) > resolved_settings.max_request_bytes:
            return openai_error(413, "Request body is too large")
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return openai_error(400, "Body must be valid JSON")
        if not isinstance(body, dict):
            return openai_error(400, "Body must be a JSON object")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return openai_error(400, "'model' must be a non-empty string")
        resolved_model = await app.state.model_catalog.resolve(model)
        if resolved_model is None:
            return openai_error(
                404,
                f"The model '{model}' does not exist or is not available.",
                param="model",
                code="model_not_found",
            )
        try:
            chat_body = responses_to_chat(body)
        except RequestValidationError as exc:
            return openai_error(400, str(exc))
        chat_body["model"] = resolved_model

        semaphore: asyncio.Semaphore = app.state.semaphore
        async with semaphore:
            try:
                upstream_response = await app.state.upstream.send(
                    chat_body,
                    stream=False,
                )
            except CredentialError as exc:
                return openai_error(
                    503,
                    str(exc),
                    error_type="authentication_error",
                )
            except RequestValidationError as exc:
                return openai_error(400, str(exc))
            except httpx.HTTPError as exc:
                return openai_error(
                    502,
                    f"Unable to reach CodeBuddy: {exc}",
                    error_type="api_connection_error",
                )
            if upstream_response.status_code >= 400:
                return await _upstream_error(upstream_response)
            payload = await upstream_response.aread()
            headers = _forward_headers(upstream_response)
            await upstream_response.aclose()

        try:
            completion = aggregate_chat_completion(payload.decode("utf-8"))
        except (SSEParseError, UnicodeDecodeError) as exc:
            return openai_error(
                502,
                f"Invalid response from CodeBuddy: {exc}",
                error_type="api_error",
            )
        result = chat_to_response(completion, body)
        if body.get("stream") is True:
            return StreamingResponse(
                response_sse(result),
                headers=headers,
                media_type="text/event-stream",
            )
        return JSONResponse(result, headers=headers)

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def unsupported_openai_endpoint(path: str) -> JSONResponse:
        return openai_error(
            404,
            f"The OpenAI-compatible endpoint '/v1/{path}' is not implemented.",
            error_type="invalid_request_error",
            code="unsupported_endpoint",
        )

    return app


def _forward_headers(response: httpx.Response) -> dict[str, str]:
    allowed = ("cache-control", "x-request-id", "x-trace-id")
    return {
        name: response.headers[name] for name in allowed if name in response.headers
    }


async def _upstream_error(response: httpx.Response) -> JSONResponse:
    status_code = response.status_code
    payload = await response.aread()
    headers = _forward_headers(response)
    await response.aclose()
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return openai_error(
            status_code,
            payload.decode("utf-8", errors="replace"),
            error_type="upstream_error",
        )
    if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
        return JSONResponse(decoded, status_code=status_code, headers=headers)
    message = (
        decoded.get("displayMsg", {}).get("en")
        or decoded.get("msg")
        or "CodeBuddy request failed"
        if isinstance(decoded, dict)
        else "CodeBuddy request failed"
    )
    code = decoded.get("code") if isinstance(decoded, dict) else None
    return openai_error(
        status_code,
        str(message),
        error_type="upstream_error",
        code=code,
    )


async def _close_stream(
    response: httpx.Response,
    semaphore: asyncio.Semaphore,
) -> None:
    try:
        await response.aclose()
    finally:
        semaphore.release()


app = create_app()
