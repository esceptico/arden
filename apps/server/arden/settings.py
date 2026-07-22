import hashlib
import hmac
import json
import secrets

from arden.brand_migration import resolve_data_dir
from arden.logging import get_logger

_logger = get_logger(__name__)

ARDEN_DIR = resolve_data_dir()


def load_user_settings() -> dict:
    path = ARDEN_DIR / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        _logger.warning("Failed to load user settings, trying backup", exc_info=True)
        backup = path.with_suffix(".json.bak")
        if backup.exists():
            try:
                data = json.loads(backup.read_text())
                _logger.info("Restored settings from backup")
                return data
            except (json.JSONDecodeError, OSError):
                _logger.warning("Backup settings also corrupted")
        return {}


def save_user_settings(settings: dict) -> None:
    ARDEN_DIR.mkdir(exist_ok=True)
    path = ARDEN_DIR / "settings.json"
    if path.exists():
        try:
            path.replace(path.with_suffix(".json.bak"))
        except OSError:
            pass
    path.write_text(json.dumps(settings, indent=2))
    path.chmod(0o600)


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
