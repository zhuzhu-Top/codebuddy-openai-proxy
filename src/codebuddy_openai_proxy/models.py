from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .credentials import (
    CredentialError,
    CredentialProvider,
    default_codebuddy_dir,
)
from .upstream import build_headers

MODEL_CACHE_SECONDS = 480.0
MODEL_ALIAS_PREFIX = "zhuzhu-"
EXTENSION_STATE_KEY = "Tencent-Cloud.coding-copilot"
MODEL_CACHE_KEY = "ACC_SHARE_CACHE_PRODUCT_MODELS"


class ModelCatalog:
    """Load the same remote and enterprise model sources used by CodeBuddy."""

    def __init__(
        self,
        settings: Settings,
        credentials: CredentialProvider,
        client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.credentials = credentials
        self.client = client
        self._models: tuple[str, ...] = ()
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, *, force: bool = False) -> tuple[str, ...]:
        now = time.monotonic()
        if not force and self._models and now < self._expires_at:
            return self._models
        async with self._lock:
            now = time.monotonic()
            if not force and self._models and now < self._expires_at:
                return self._models

            remote = await self._load_remote_models()
            local = load_cached_enterprise_models()
            merged = tuple(dict.fromkeys((*remote, *local)))
            if merged:
                self._models = merged
                self._expires_at = now + MODEL_CACHE_SECONDS
            elif not self._models:
                self._models = self.settings.models
                self._expires_at = now + 30.0
            return self._models

    async def public_ids(self, *, force: bool = False) -> tuple[str, ...]:
        aliases = (model_alias(model) for model in await self.get(force=force))
        return tuple(dict.fromkeys(aliases))

    async def resolve(self, requested: str) -> str | None:
        models = await self.get()
        if requested in models:
            return requested
        normalized = (
            requested[len("custom:") :]
            if requested.startswith("custom:")
            else requested
        )
        for model in models:
            if normalized in {model_alias(model), _model_short_name(model)}:
                return model
        return None

    async def _load_remote_models(self) -> tuple[str, ...]:
        try:
            credentials = self.credentials.get()
            response = await self.client.get(
                self.settings.config_url,
                headers=build_headers(self.settings, credentials, ""),
            )
            if response.status_code == 401:
                credentials = self.credentials.get(force=True)
                response = await self.client.get(
                    self.settings.config_url,
                    headers=build_headers(self.settings, credentials, ""),
                )
            response.raise_for_status()
            payload = response.json()
        except (CredentialError, httpx.HTTPError, ValueError):
            return ()

        data = payload.get("data") if isinstance(payload, dict) else None
        models = data.get("models") if isinstance(data, dict) else None
        return _model_ids(models)


def load_cached_enterprise_models(state_db: Path | None = None) -> tuple[str, ...]:
    database = state_db or (default_codebuddy_dir() / "User/globalStorage/state.vscdb")
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro",
            uri=True,
            timeout=2,
        )
        try:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                (EXTENSION_STATE_KEY,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return ()
        state = json.loads(row[0])
        cache = state.get(MODEL_CACHE_KEY)
        if not isinstance(cache, dict):
            return ()
        entries = [entry for entry in cache.values() if isinstance(entry, dict)]
        if not entries:
            return ()
        latest = max(entries, key=lambda entry: entry.get("timestamp", 0))
        value = latest.get("value")
        models = value.get("models") if isinstance(value, dict) else None
        return _model_ids(models)
    except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
        return ()


def _model_ids(models: Any) -> tuple[str, ...]:
    if not isinstance(models, list):
        return ()
    result: list[str] = []
    for model in models:
        if not isinstance(model, dict) or model.get("disabled") is True:
            continue
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id:
            result.append(model_id)
    return tuple(result)


def model_alias(model_id: str) -> str:
    return f"{MODEL_ALIAS_PREFIX}{_model_short_name(model_id)}"


def _model_short_name(model_id: str) -> str:
    name = model_id.rsplit("|", 1)[-1]
    if name.startswith("custom:"):
        name = name[len("custom:") :]
    return name
