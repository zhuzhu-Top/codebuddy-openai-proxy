"""Compatibility command: credentials are now loaded automatically in memory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from codebuddy_openai_proxy.credentials import WindowsCredentialProvider  # noqa: E402


def main() -> int:
    result = WindowsCredentialProvider().diagnose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Credentials were checked in memory and were not written to disk.")
    return 0 if result.get("authenticated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
