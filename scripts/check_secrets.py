from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PATTERNS = {
    "populated upstream token": re.compile(
        r"(?m)^CODEBUDDY_(?:ACCESS|DEVICE)_TOKEN=.+$"
    ),
    "JWT-like bearer token": re.compile(r"Bearer\s+eyJ[A-Za-z0-9_-]{40,}"),
    "user-specific Windows path": re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+"),
}


def iter_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part in IGNORED for part in path.relative_to(ROOT).parts)
        and path.name != ".env"
    ]


def main() -> int:
    findings: list[str] = []
    if (ROOT / ".env").exists():
        findings.append(".env exists in the project directory")
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        print("Sensitive-data check failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Sensitive-data check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
