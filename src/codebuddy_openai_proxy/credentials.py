from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AUTH_SECRET_KEY = (
    'secret://{"extensionId":"tencent-cloud.coding-copilot",'
    '"key":"planning-genie.new.accessTokencn"}'
)


class CredentialError(RuntimeError):
    """Raised when a usable local CodeBuddy session cannot be loaded."""


@dataclass(frozen=True)
class Credentials:
    access_token: str
    user_id: str = ""
    enterprise_id: str = ""
    domain: str = ""
    device_token: str = ""

    @property
    def is_enterprise(self) -> bool:
        return bool(self.enterprise_id)


class CredentialProvider(Protocol):
    def get(self, *, force: bool = False) -> Credentials: ...

    def diagnose(self) -> dict[str, Any]: ...


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def default_codebuddy_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "CodeBuddy CN"
    return Path.home() / "AppData/Roaming/CodeBuddy CN"


def dpapi_decrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialError(
            "Automatic credential loading is supported on Windows only"
        )
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    destination = DataBlob()
    result = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(destination),
    )
    if not result:
        raise CredentialError(str(ctypes.WinError()))
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)


def decrypt_electron_secret(encrypted: bytes, master_key: bytes) -> str:
    if encrypted.startswith((b"v10", b"v11")):
        nonce = encrypted[3:15]
        plaintext = AESGCM(master_key).decrypt(nonce, encrypted[15:], None)
    else:
        plaintext = dpapi_decrypt(encrypted)
    return plaintext.decode("utf-8")


def _find_string(value: Any, names: set[str]) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.replace("_", "").replace("-", "").lower()
            if normalized in names and isinstance(child, str) and child:
                return child
        for child in value.values():
            found = _find_string(child, names)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_string(child, names)
            if found:
                return found
    elif isinstance(value, str):
        try:
            return _find_string(json.loads(value), names)
        except (json.JSONDecodeError, TypeError):
            return ""
    return ""


class WindowsCredentialProvider:
    """Read the current user's CodeBuddy session without persisting plaintext."""

    def __init__(self, codebuddy_dir: Path | None = None) -> None:
        self.codebuddy_dir = codebuddy_dir or default_codebuddy_dir()
        self.state_db = self.codebuddy_dir / "User/globalStorage/state.vscdb"
        self.local_state = self.codebuddy_dir / "Local State"
        self.storage_json = self.codebuddy_dir / "User/globalStorage/storage.json"
        self._cached: Credentials | None = None
        self._cache_stamp: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def get(self, *, force: bool = False) -> Credentials:
        environment = self._from_environment()
        if environment:
            return environment
        with self._lock:
            stamp = self._stamp()
            if not force and self._cached and stamp == self._cache_stamp:
                return self._cached
            credentials = self._load()
            self._cached = credentials
            self._cache_stamp = stamp
            return credentials

    def diagnose(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "platform": os.name,
            "codebuddy_dir_exists": self.codebuddy_dir.exists(),
            "state_db_exists": self.state_db.exists(),
            "local_state_exists": self.local_state.exists(),
        }
        try:
            credentials = self.get(force=True)
            result.update(
                {
                    "authenticated": True,
                    "enterprise": credentials.is_enterprise,
                    "device_token_available": bool(credentials.device_token),
                    "user_id_available": bool(credentials.user_id),
                }
            )
        except CredentialError as exc:
            result.update({"authenticated": False, "error": str(exc)})
        return result

    def _stamp(self) -> tuple[int, int]:
        try:
            return (
                self.state_db.stat().st_mtime_ns,
                self.local_state.stat().st_mtime_ns,
            )
        except OSError as exc:
            raise CredentialError(
                "CodeBuddy user data was not found; install and sign in first"
            ) from exc

    def _from_environment(self) -> Credentials | None:
        access_token = os.getenv("CODEBUDDY_ACCESS_TOKEN", "").strip()
        if not access_token:
            return None
        return Credentials(
            access_token=access_token,
            user_id=os.getenv("CODEBUDDY_USER_ID", "").strip(),
            enterprise_id=os.getenv("CODEBUDDY_ENTERPRISE_ID", "").strip(),
            domain=os.getenv("CODEBUDDY_DOMAIN", "").strip(),
            device_token=os.getenv("CODEBUDDY_DEVICE_TOKEN", "").strip(),
        )

    def _load(self) -> Credentials:
        master_key = self._load_master_key()
        auth = self._load_secret(AUTH_SECRET_KEY, master_key)
        auth_data = auth.get("auth") if isinstance(auth, dict) else None
        if not isinstance(auth_data, dict):
            raise CredentialError("CodeBuddy session has an unexpected structure")
        access_token = _find_string(auth_data, {"accesstoken"})
        if not access_token:
            raise CredentialError("CodeBuddy access token was not found; sign in again")

        storage = self._load_storage()
        account = auth.get("account") if isinstance(auth.get("account"), dict) else {}
        user_id = str(account.get("uid") or storage.get("genie.userId") or "")
        enterprise_id = str(
            account.get("enterpriseId") or storage.get("genie.enterpriseId") or ""
        )
        device_token = os.getenv("CODEBUDDY_DEVICE_TOKEN", "").strip()
        if not enterprise_id and not device_token:
            device_token = self._find_cached_device_token(master_key)
        if not enterprise_id and not device_token:
            raise CredentialError(
                "This personal account requires X-Device-Token. Set "
                "CODEBUDDY_DEVICE_TOKEN from a successful local CodeBuddy request."
            )
        return Credentials(
            access_token=access_token,
            user_id=user_id,
            enterprise_id=enterprise_id,
            domain=str(auth_data.get("domain") or ""),
            device_token=device_token,
        )

    def _load_master_key(self) -> bytes:
        try:
            state = json.loads(self.local_state.read_text(encoding="utf-8"))
            encrypted = base64.b64decode(state["os_crypt"]["encrypted_key"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise CredentialError("Unable to read CodeBuddy's encryption key") from exc
        if not encrypted.startswith(b"DPAPI"):
            raise CredentialError("Unsupported CodeBuddy encryption-key format")
        return dpapi_decrypt(encrypted[5:])

    def _load_secret(self, key: str, master_key: bytes) -> Any:
        try:
            connection = sqlite3.connect(
                f"file:{self.state_db}?mode=ro",
                uri=True,
                timeout=2,
            )
            try:
                row = connection.execute(
                    "SELECT value FROM ItemTable WHERE key = ?",
                    (key,),
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                raise CredentialError("CodeBuddy login secret was not found")
            encrypted = bytes(json.loads(row[0])["data"])
            return json.loads(decrypt_electron_secret(encrypted, master_key))
        except CredentialError:
            raise
        except (
            OSError,
            KeyError,
            ValueError,
            sqlite3.Error,
            json.JSONDecodeError,
        ) as exc:
            raise CredentialError("Unable to decrypt CodeBuddy login data") from exc

    def _find_cached_device_token(self, master_key: bytes) -> str:
        try:
            connection = sqlite3.connect(
                f"file:{self.state_db}?mode=ro", uri=True, timeout=2
            )
            try:
                rows = connection.execute(
                    "SELECT key FROM ItemTable "
                    "WHERE lower(key) LIKE '%device%' OR lower(key) LIKE '%qimei%'"
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            return ""
        for (key,) in rows:
            try:
                value = self._load_secret(key, master_key)
            except CredentialError:
                continue
            token = _find_string(value, {"devicetoken", "qimei36", "qimei"})
            if token:
                return token
        return ""

    def _load_storage(self) -> dict[str, Any]:
        try:
            value = json.loads(self.storage_json.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
