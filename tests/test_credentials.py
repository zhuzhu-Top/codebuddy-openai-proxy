from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from codebuddy_openai_proxy.credentials import (
    WindowsCredentialProvider,
    decrypt_electron_secret,
)


def test_decrypt_electron_v10_secret() -> None:
    key = os.urandom(32)
    nonce = os.urandom(12)
    plaintext = b'{"auth":{"accessToken":"secret"}}'
    encrypted = b"v10" + nonce + AESGCM(key).encrypt(nonce, plaintext, None)
    assert decrypt_electron_secret(encrypted, key) == plaintext.decode()


def test_environment_credentials_override_disk(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEBUDDY_ACCESS_TOKEN", "token")
    monkeypatch.setenv("CODEBUDDY_USER_ID", "user")
    monkeypatch.setenv("CODEBUDDY_ENTERPRISE_ID", "tenant")
    monkeypatch.setenv("CODEBUDDY_DOMAIN", "example.invalid")
    provider = WindowsCredentialProvider(tmp_path / "missing")

    credentials = provider.get()
    assert credentials.access_token == "token"
    assert credentials.user_id == "user"
    assert credentials.enterprise_id == "tenant"
    assert credentials.is_enterprise
