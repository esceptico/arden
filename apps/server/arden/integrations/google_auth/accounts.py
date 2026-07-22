import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

GoogleService = Literal["gmail", "calendar", "google_drive"]

_GMAIL_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
}
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DRIVE_SCOPES = {
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
}


@dataclass(frozen=True)
class GoogleAuthorization:
    service: GoogleService
    token_file: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class GoogleAccount:
    id: str
    email: str | None
    token_file: str
    scopes: tuple[str, ...]
    services: frozenset[GoogleService]
    authorizations: dict[GoogleService, GoogleAuthorization] = field(default_factory=dict)


class GoogleAccountStore:
    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "google_accounts.json"
        self.token_dir = root / "google_tokens"
        self._lock = RLock()

    def _read_records(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        data = json.loads(self.index_path.read_text())
        records = data.get("accounts", []) if isinstance(data, dict) else []
        return [self._normalize_record(record) for record in records if isinstance(record, dict)]

    @staticmethod
    def _sync_legacy_fields(record: dict) -> None:
        authorizations = record.get("authorizations", {})
        record["services"] = sorted(authorizations)
        record["scopes"] = sorted(
            {str(scope) for authorization in authorizations.values() for scope in authorization.get("scopes", [])}
        )
        if authorizations:
            first_service = sorted(authorizations)[0]
            record["token_file"] = authorizations[first_service]["token_file"]

    @classmethod
    def _normalize_record(cls, record: dict) -> dict:
        normalized = dict(record)
        raw_authorizations = normalized.get("authorizations")
        if not isinstance(raw_authorizations, dict):
            token_file = str(normalized.get("token_file") or "")
            scopes = [str(scope) for scope in normalized.get("scopes", [])]
            raw_authorizations = {
                str(service): {"token_file": token_file, "scopes": scopes}
                for service in normalized.get("services", [])
                if token_file
            }
        normalized["authorizations"] = {
            str(service): {
                "token_file": str(authorization.get("token_file") or ""),
                "scopes": sorted({str(scope) for scope in authorization.get("scopes", [])}),
            }
            for service, authorization in raw_authorizations.items()
            if service in {"gmail", "calendar", "google_drive"}
            and isinstance(authorization, dict)
            and authorization.get("token_file")
        }
        cls._sync_legacy_fields(normalized)
        return normalized

    @staticmethod
    def _account(record: dict) -> GoogleAccount:
        authorizations = {
            service: GoogleAuthorization(
                service=service,
                token_file=str(authorization["token_file"]),
                scopes=tuple(str(scope) for scope in authorization.get("scopes", [])),
            )
            for service, authorization in record.get("authorizations", {}).items()
        }
        return GoogleAccount(
            id=str(record["id"]),
            email=str(record["email"]) if record.get("email") else None,
            token_file=str(record["token_file"]),
            scopes=tuple(str(scope) for scope in record.get("scopes", [])),
            services=frozenset(record.get("services", [])),
            authorizations=authorizations,
        )

    def list_accounts(self) -> list[GoogleAccount]:
        with self._lock:
            return [self._account(record) for record in self._read_records()]

    def accounts_for(self, service: GoogleService) -> list[GoogleAccount]:
        return [account for account in self.list_accounts() if service in account.services]

    def is_bound(self, service: GoogleService) -> bool:
        return bool(self.accounts_for(service))

    def token_path(self, account: GoogleAccount, service: GoogleService | None = None) -> Path:
        if service is not None:
            authorization = account.authorizations.get(service)
            if authorization is None:
                raise KeyError(f"Google account {account.id} is not authorized for {service}")
            return self.root / authorization.token_file
        return self.root / account.token_file

    def token_paths(self, account: GoogleAccount) -> list[Path]:
        token_files = {authorization.token_file for authorization in account.authorizations.values()}
        if not token_files and account.token_file:
            token_files.add(account.token_file)
        return [self.root / token_file for token_file in sorted(token_files)]

    @staticmethod
    def _write_private(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(content)
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _write_records(self, records: list[dict]) -> None:
        payload = json.dumps({"version": 2, "accounts": records}, indent=2, sort_keys=True)
        self._write_private(self.index_path, payload)

    def upsert_authorization(
        self,
        *,
        email: str | None,
        credential_json: str,
        scopes: tuple[str, ...],
        service: GoogleService,
        account_id: str | None = None,
        legacy_source: str | None = None,
    ) -> GoogleAccount:
        with self._lock:
            records = self._read_records()
            record = next((item for item in records if account_id and item.get("id") == account_id), None)
            if record is None and email:
                normalized = email.casefold()
                record = next(
                    (item for item in records if str(item.get("email") or "").casefold() == normalized),
                    None,
                )
            if record is None and legacy_source:
                record = next(
                    (item for item in records if legacy_source in item.get("legacy_sources", [])),
                    None,
                )
            if record is None:
                resolved_id = account_id or uuid4().hex
                record = {
                    "id": resolved_id,
                    "email": email,
                    "authorizations": {},
                    "legacy_sources": [],
                }
                records.append(record)

            if email:
                record["email"] = email
            authorizations = record.setdefault("authorizations", {})
            current = authorizations.get(service)
            shared = bool(current) and any(
                other_service != service and authorization.get("token_file") == current.get("token_file")
                for other_service, authorization in authorizations.items()
            )
            token_file = (
                str(current["token_file"]) if current and not shared else f"google_tokens/{record['id']}-{service}.json"
            )
            authorizations[service] = {
                "token_file": token_file,
                "scopes": sorted(set(scopes)),
            }
            self._sync_legacy_fields(record)
            if legacy_source:
                record["legacy_sources"] = sorted(set(record.get("legacy_sources", [])) | {legacy_source})

            self._write_private(self.root / token_file, credential_json)
            self._write_records(records)
            return self._account(record)

    def bind_service(self, account_id: str, service: GoogleService) -> GoogleAccount:
        with self._lock:
            records = self._read_records()
            record = next((item for item in records if item.get("id") == account_id), None)
            if record is None:
                raise KeyError(f"Unknown Google account: {account_id}")
            authorizations = record.setdefault("authorizations", {})
            if service not in authorizations:
                source = next(iter(authorizations.values()), None)
                if source is None:
                    raise KeyError(f"Google account {account_id} has no authorization to bind")
                authorizations[service] = dict(source)
            self._sync_legacy_fields(record)
            self._write_records(records)
            return self._account(record)

    def disconnect_service(self, account_id: str, service: GoogleService) -> GoogleAccount:
        with self._lock:
            records = self._read_records()
            record = next((item for item in records if item.get("id") == account_id), None)
            if record is None:
                raise KeyError(f"Unknown Google account: {account_id}")
            record.setdefault("authorizations", {}).pop(service, None)
            self._sync_legacy_fields(record)
            self._write_records(records)
            return self._account(record)

    def remove_account(self, account_id: str) -> GoogleAccount:
        with self._lock:
            records = self._read_records()
            record = next((item for item in records if item.get("id") == account_id), None)
            if record is None:
                raise KeyError(f"Unknown Google account: {account_id}")
            records.remove(record)
            self._write_records(records)
            token_files = {
                str(authorization.get("token_file"))
                for authorization in record.get("authorizations", {}).values()
                if authorization.get("token_file")
            }
            if record.get("token_file"):
                token_files.add(str(record["token_file"]))
            for token_file in token_files:
                token_path = self.root / token_file
                if token_path.exists():
                    token_path.unlink()
            return self._account(record)

    @staticmethod
    def _services_for_scopes(scopes: set[str]) -> tuple[GoogleService, ...]:
        services: list[GoogleService] = []
        if scopes & _GMAIL_SCOPES:
            services.append("gmail")
        if _CALENDAR_SCOPE in scopes:
            services.append("calendar")
        if scopes & _DRIVE_SCOPES:
            services.append("google_drive")
        return tuple(services)

    def migrate_legacy(self) -> None:
        paths = sorted((*self.root.glob("gmail_token*.json"), *self.root.glob("calendar_token*.json")))
        for path in paths:
            if any(path.name in record.get("legacy_sources", []) for record in self._read_records()):
                continue
            try:
                credential_json = path.read_text()
                data = json.loads(credential_json)
                scopes = tuple(str(scope) for scope in data.get("scopes", []))
            except (OSError, ValueError, TypeError):
                continue
            services = self._services_for_scopes(set(scopes))
            if not services:
                continue
            email = None
            if path.stem.startswith("gmail_token_"):
                email = path.stem.removeprefix("gmail_token_") or None
            account = self.upsert_authorization(
                email=email,
                credential_json=credential_json,
                scopes=scopes,
                service=services[0],
                legacy_source=path.name,
            )
            for service in services[1:]:
                self.bind_service(account.id, service)
