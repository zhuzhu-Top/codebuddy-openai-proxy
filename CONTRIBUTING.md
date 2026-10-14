# Contributing

Contributions are welcome. Keep changes focused and avoid including account
data, captured headers, tokens, local paths, or proprietary application files.

## Development

On Windows with Python 3.10 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy
python scripts/check_secrets.py
```

Unit tests must not require a real CodeBuddy account or network access. Mock
the upstream transport and credential provider. Manual integration testing
must use only your own account and must not record secrets in fixtures or
logs.

## Pull requests

Explain the user-visible behavior, add or update tests, and update
`CHANGELOG.md` when appropriate. All CI checks must pass. By contributing, you
agree that your contribution is licensed under the MIT License.
