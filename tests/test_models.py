from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from codebuddy_openai_proxy.config import Settings
from codebuddy_openai_proxy.credentials import Credentials
from codebuddy_openai_proxy.models import (
    ModelCatalog,
    load_cached_enterprise_models,
    model_alias,
)


class FakeCredentials:
    def get(self, *, force: bool = False) -> Credentials:
        return Credentials(access_token="token", enterprise_id="tenant")

    def diagnose(self) -> dict[str, Any]:
        return {"authenticated": True}


def test_model_alias_is_cursor_safe() -> None:
    model = "custom:gpt-5.6-sol|gpt-5.6-sol"
    assert model_alias(model) == "zhuzhu-gpt-5.6-sol"


@pytest.mark.asyncio
async def test_resolves_codex_bare_model_name() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    settings = Settings(
        models=("custom:gpt-5.6-sol|gpt-5.6-sol",),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalog = ModelCatalog(settings, FakeCredentials(), client)
        assert await catalog.resolve("gpt-5.6-sol") == (
            "custom:gpt-5.6-sol|gpt-5.6-sol"
        )


def test_loads_latest_enterprise_model_cache(tmp_path: Path) -> None:
    database = tmp_path / "state.vscdb"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    state = {
        "ACC_SHARE_CACHE_PRODUCT_MODELS": {
            "old": {
                "timestamp": 1,
                "value": {"models": [{"id": "old-model"}]},
            },
            "new": {
                "timestamp": 2,
                "value": {
                    "models": [
                        {"id": "enterprise-model"},
                        {"id": "disabled-model", "disabled": True},
                    ]
                },
            },
        }
    }
    connection.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        ("Tencent-Cloud.coding-copilot", json.dumps(state)),
    )
    connection.commit()
    connection.close()

    assert load_cached_enterprise_models(database) == ("enterprise-model",)


@pytest.mark.asyncio
async def test_fetches_account_models_from_remote_config() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/config"
        assert request.headers["user-agent"].startswith("CodeBuddyIDE/")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "models": [
                        {"id": "account-model"},
                        {"id": "disabled-model", "disabled": True},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = await ModelCatalog(Settings(), FakeCredentials(), client).get()

    assert "account-model" in models
    assert "disabled-model" not in models
