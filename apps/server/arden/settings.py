import hashlib
import hmac
import json
import secrets

from arden.atomic_file import write_private_atomic
from arden.constants import ARDEN_DIR

# Re-exported: ARDEN_DIR's canonical home is arden.constants, but config.py and
# others have always imported it from here.
__all__ = ["ARDEN_DIR"]


class SettingsError(RuntimeError):
    """The persisted user settings cannot be read as the current contract."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def load_user_settings() -> dict:
    path = ARDEN_DIR / "settings.json"
    backup = path.with_suffix(".json.bak")
    if not path.exists():
        if backup.exists():
            raise SettingsError(f"User settings are missing at {path}; manual recovery is available at {backup}")
        return {}
    try:
        settings = json.loads(path.read_text(), parse_constant=_reject_nonfinite_json)
    except (json.JSONDecodeError, OSError, ValueError) as error:
        raise SettingsError(f"User settings at {path} are unreadable or invalid JSON") from error
    if not isinstance(settings, dict):
        raise SettingsError(f"User settings at {path} must be a JSON object")
    return settings


def save_user_settings(settings: dict) -> None:
    payload = json.dumps(settings, indent=2, allow_nan=False).encode()
    ARDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = ARDEN_DIR / "settings.json"
    if path.exists():
        write_private_atomic(path.with_suffix(".json.bak"), path.read_bytes())
    write_private_atomic(path, payload)


def restore_user_settings(settings: dict) -> None:
    payload = json.dumps(settings, indent=2, allow_nan=False).encode()
    write_private_atomic(ARDEN_DIR / "settings.json", payload)


# --- API key auth ---


def mask_api_key(key: str | None) -> str | None:
    if not key or len(key) < 8:
        return "****" if key else None
    return key[:4] + "..." + key[-4:]


def _hash_key(key: str, salt: bytes) -> str:
    return hashlib.sha256(salt + key.encode()).hexdigest()


def hash_api_key(key: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_hash_key(key, salt)}"


def verify_api_key(key: str, stored_hash: str) -> bool:
    try:
        salt_hex, h = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        return hmac.compare_digest(_hash_key(key, salt), h)
    except (ValueError, IndexError):
        return False


def generate_api_key() -> tuple[str, str]:
    key = secrets.token_urlsafe(32)
    return key, hash_api_key(key)
