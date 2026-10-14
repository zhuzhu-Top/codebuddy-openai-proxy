# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

- Discover models from the authenticated CodeBuddy product configuration and
  merge locally cached enterprise custom models.
- Expose Cursor-safe model aliases with a fixed `zhuzhu-` prefix and accept
  Cursor's automatic `custom:` prefix.
- Encode replayed assistant tool call arguments sent as JSON objects, which
  CodeBuddy otherwise rejects with error 11101.

## [0.2.0] - 2026-10-14

- Added an installable CLI with `serve`, `doctor`, and `models` commands.
- Added automatic Windows CodeBuddy credential loading using DPAPI and
  Electron Secret Storage, with in-memory refresh after authentication errors.
- Added OpenAI-compatible chat completions, model listing, streaming, and
  aggregation of upstream SSE into non-streaming responses.
- Added configurable reasoning effort, timeouts, concurrency, and request-size
  limits.
- Added loopback-only defaults, tests, Windows CI, static checks, and
  sensitive-data scanning.
- Removed local API-key authentication; the loopback service accepts requests
  directly.
