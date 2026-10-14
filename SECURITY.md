# Security Policy

## Supported versions

Security fixes are applied to the latest released version and the default
branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature. Do not open a
public issue containing access tokens, device tokens, decrypted CodeBuddy
storage, request headers, or other account information.

Include the affected version, impact, reproduction steps, and a minimal
redacted example. Maintainers will acknowledge a report when practical and
coordinate disclosure after a fix is available.

## Security model

- The proxy has no local authentication and is intended to bind to loopback
  only. Exposing it to a network grants network users access to the connected
  account.
- CodeBuddy credentials are decrypted for the current Windows user and kept
  in process memory. The project does not persist their plaintext.
- Environment overrides and `.env` files are the operator's responsibility.
  Never commit either credentials or captured traffic.

This project is unofficial and cannot provide security support for CodeBuddy
or OpenAI services themselves.
