from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import uvicorn
from dotenv import load_dotenv

from .app import create_app
from .config import Settings
from .credentials import WindowsCredentialProvider
from .models import ModelCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebuddy-openai-proxy",
        description="Local OpenAI-compatible proxy for CodeBuddy",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="start the local proxy")
    serve.add_argument("--host", help="listen address")
    serve.add_argument("--port", type=int, help="listen port")

    subparsers.add_parser(
        "doctor",
        help="check local CodeBuddy authentication without showing secrets",
    )
    subparsers.add_parser("models", help="show configured model IDs")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    command = args.command or "serve"
    settings = Settings.from_env()

    if command == "doctor":
        result = WindowsCredentialProvider().diagnose()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("authenticated") else 1

    if command == "models":
        for model in asyncio.run(_load_models(settings)):
            print(model)
        return 0

    host = args.host or settings.host
    port = args.port or settings.port
    if host not in {"127.0.0.1", "::1", "localhost"}:
        print(
            "WARNING: exposing this proxy can grant network users access to "
            "your CodeBuddy account.",
            file=sys.stderr,
        )
    print(f"Base URL: http://{host}:{port}/v1")
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        log_level="info",
    )
    return 0


async def _load_models(settings: Settings) -> tuple[str, ...]:
    timeout = httpx.Timeout(settings.timeout, connect=settings.connect_timeout)
    provider = WindowsCredentialProvider()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        return await ModelCatalog(settings, provider, client).public_ids()


if __name__ == "__main__":
    raise SystemExit(main())
