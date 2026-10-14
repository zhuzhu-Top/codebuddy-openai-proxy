from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_UPSTREAM = "https://copilot.tencent.com/v2/chat/completions"
DEFAULT_CONFIG_URL = "https://copilot.tencent.com/v3/config"
DEFAULT_MODELS = (
    "custom:gpt-5.6-sol|gpt-5.6-sol",
    "kimi-k2-instruct-taiji",
)
REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


@dataclass(frozen=True)
class Settings:
    upstream: str = DEFAULT_UPSTREAM
    config_url: str = DEFAULT_CONFIG_URL
    models: tuple[str, ...] = DEFAULT_MODELS
    timeout: float = 600.0
    connect_timeout: float = 30.0
    max_request_bytes: int = 10 * 1024 * 1024
    max_concurrency: int = 8
    host: str = "127.0.0.1"
    port: int = 8765
    ide_version: str = "1.106.1"
    product_version: str = "4.12.2"

    @classmethod
    def from_env(cls) -> Settings:
        models = tuple(
            item.strip()
            for item in os.getenv("CODEBUDDY_MODELS", ",".join(DEFAULT_MODELS)).split(
                ","
            )
            if item.strip()
        )
        return cls(
            upstream=os.getenv("CODEBUDDY_UPSTREAM", DEFAULT_UPSTREAM).strip(),
            config_url=os.getenv("CODEBUDDY_CONFIG_URL", DEFAULT_CONFIG_URL).strip(),
            models=models or DEFAULT_MODELS,
            timeout=float(os.getenv("CODEBUDDY_TIMEOUT", "600")),
            connect_timeout=float(os.getenv("CODEBUDDY_CONNECT_TIMEOUT", "30")),
            max_request_bytes=int(
                os.getenv("CODEBUDDY_MAX_REQUEST_BYTES", str(10 * 1024 * 1024))
            ),
            max_concurrency=max(1, int(os.getenv("CODEBUDDY_MAX_CONCURRENCY", "8"))),
            host=os.getenv("HOST", "127.0.0.1").strip(),
            port=int(os.getenv("PORT", "8765")),
            ide_version=os.getenv("CODEBUDDY_IDE_VERSION", "1.106.1").strip(),
            product_version=os.getenv("CODEBUDDY_PRODUCT_VERSION", "4.12.2").strip(),
        )
