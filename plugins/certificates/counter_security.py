"""Local PIN protection for administrative certificate-counter changes."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

__author__ = "Rickard Längkvist"
_START_YEAR = 2025
_CURRENT_YEAR = datetime.now().year
COPYRIGHT = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_PIN_FORMAT = re.compile(r"[0-9]{4,12}\Z")
_PIN_ITERATIONS = 310_000
_PIN_SALT_BYTES = 16
_PIN_DIGEST_BYTES = 32


class CounterPinStore:
    """Persist only a salted PBKDF2 hash; the PIN itself is never written to disk."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @property
    def configured(self) -> bool:
        return self.path.exists()

    @staticmethod
    def validate(pin: str) -> bool:
        return bool(_PIN_FORMAT.fullmatch(str(pin)))

    def set_pin(self, pin: str) -> None:
        if not self.validate(pin):
            raise ValueError("PIN-koden måste bestå av 4–12 siffror.")
        salt = secrets.token_bytes(_PIN_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("ascii"), salt, _PIN_ITERATIONS, dklen=_PIN_DIGEST_BYTES
        )
        payload = {
            "version": 1,
            "algorithm": "pbkdf2_sha256",
            "iterations": _PIN_ITERATIONS,
            "salt": salt.hex(),
            "digest": digest.hex(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, pin: str) -> bool:
        if not self.path.is_file() or not self.validate(pin):
            return False
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("algorithm") != "pbkdf2_sha256":
                return False
            iterations = payload.get("iterations")
            if isinstance(iterations, bool) or not isinstance(iterations, int):
                return False
            if not 100_000 <= iterations <= 2_000_000:
                return False
            salt = bytes.fromhex(str(payload["salt"]))
            expected = bytes.fromhex(str(payload["digest"]))
            if len(salt) != _PIN_SALT_BYTES or len(expected) != _PIN_DIGEST_BYTES:
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256", pin.encode("ascii"), salt, iterations, dklen=_PIN_DIGEST_BYTES
            )
            return hmac.compare_digest(actual, expected)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            # A present but damaged PIN file never silently becomes an unprotected setup.
            return False
