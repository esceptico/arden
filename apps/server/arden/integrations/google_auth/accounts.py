import json
from dataclasses import dataclass
from email.errors import HeaderParseError, InvalidHeaderDefect
from email.headerregistry import Address
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arden.integrations.google_auth.storage import write_private_bytes, write_private_text

GoogleService = Literal["gmail", "calendar", "google_drive"]


def validate_google_account_email(value: str) -> str:
    if not value or value != value.strip() or len(value) > 320:
        raise ValueError("email must be an exact non-blank address")
    try:
        Address(addr_spec=value)
    except (HeaderParseError, InvalidHeaderDefect) as exc:
        raise ValueError("email must be a valid address") from exc
    return value


class GoogleAccountStoreCorruption(ValueError):
    pass


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


class _AuthorizationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    token_file: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)

    @field_validator("token_file")
    @classmethod
    def _safe_token_file(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            "\\" in value
            or path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "google_tokens"
            or path.name in {"", ".", ".."}
            or path.suffix != ".json"
            or path.as_posix() != value
        ):
            raise ValueError("token_file must be a canonical path inside google_tokens")
        return value

    @field_validator("scopes")
    @classmethod
    def _canonical_scopes(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("scopes must contain exact non-blank strings")
        if values != sorted(set(values)):
            raise ValueError("scopes must be sorted and unique")
        return values


class _AccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    email: str
    authorizations: dict[GoogleService, _AuthorizationRecord]

    @field_validator("email")
    @classmethod
    def _exact_email(cls, value: str) -> str:
        return validate_google_account_email(value)


class _AccountIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[2]
    accounts: list[_AccountRecord]

    @model_validator(mode="after")
    def _unique_accounts(self) -> "_AccountIndex":
        account_ids = [account.id for account in self.accounts]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account ids must be unique")
        emails = [account.email.casefold() for account in self.accounts]
        if len(emails) != len(set(emails)):
            raise ValueError("account emails must be unique")
        return self


@dataclass(frozen=True, slots=True)
class GoogleAuthorization:
    service: GoogleService
    token_file: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.service not in {"gmail", "calendar", "google_drive"}:
            raise ValueError(f"Unknown Google service: {self.service}")
        _AuthorizationRecord(
            token_file=self.token_file,
            scopes=list(self.scopes),
        )


@dataclass(frozen=True, slots=True)
class GoogleAccount:
    id: str
    email: str
    authorizations: tuple[GoogleAuthorization, ...]

    def __post_init__(self) -> None:
        _AccountRecord(id=self.id, email=self.email, authorizations={})
        services = [authorization.service for authorization in self.authorizations]
        if len(services) != len(set(services)):
            raise ValueError("Google account authorizations must use unique services")

    @property
    def services(self) -> frozenset[GoogleService]:
        return frozenset(authorization.service for authorization in self.authorizations)

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted({scope for authorization in self.authorizations for scope in authorization.scopes}))

    def authorization(self, service: GoogleService) -> GoogleAuthorization:
        for authorization in self.authorizations:
            if authorization.service == service:
                return authorization
        raise KeyError(f"Google account {self.id} is not authorized for {service}")


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    content: bytes | None


@dataclass(frozen=True, slots=True)
class GoogleAccountStoreSnapshot:
    index: _FileSnapshot
    tokens: tuple[tuple[str, _FileSnapshot], ...]


class GoogleAccountStore:
    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "google_accounts.json"
        self._lock = RLock()

    def _read_index(self) -> _AccountIndex:
        if not self.index_path.exists():
            return _AccountIndex(version=2, accounts=[])
        try:
            raw = json.loads(
                self.index_path.read_text(),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise GoogleAccountStoreCorruption("Google account index is not valid JSON") from exc
        except _DuplicateJsonKey as exc:
            raise GoogleAccountStoreCorruption(f"Google account index contains duplicate key: {exc}") from exc
        try:
            return _AccountIndex.model_validate(raw, strict=True)
        except ValidationError as exc:
            raise GoogleAccountStoreCorruption("Google account index is not canonical version 2") from exc

    @staticmethod
    def _account(record: _AccountRecord) -> GoogleAccount:
        return GoogleAccount(
            id=record.id,
            email=record.email,
            authorizations=tuple(
                GoogleAuthorization(
                    service=service,
                    token_file=authorization.token_file,
                    scopes=tuple(authorization.scopes),
                )
                for service, authorization in sorted(record.authorizations.items())
            ),
        )

    def list_accounts(self) -> list[GoogleAccount]:
        with self._lock:
            return [self._account(record) for record in self._read_index().accounts]

    def accounts_for(self, service: GoogleService) -> list[GoogleAccount]:
        return [account for account in self.list_accounts() if service in account.services]

    def is_bound(self, service: GoogleService) -> bool:
        return bool(self.accounts_for(service))

    def token_path(self, account: GoogleAccount, service: GoogleService) -> Path:
        return self.root / account.authorization(service).token_file

    def token_paths(self, account: GoogleAccount) -> list[Path]:
        token_files = {authorization.token_file for authorization in account.authorizations}
        return [self.root / token_file for token_file in sorted(token_files)]

    def _write_index(self, index: _AccountIndex) -> None:
        payload = json.dumps(
            index.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        write_private_text(self.index_path, payload)

    @staticmethod
    def _snapshot(path: Path) -> _FileSnapshot:
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return _FileSnapshot(content=None)
        return _FileSnapshot(content=content)

    def _restore(self, path: Path, snapshot: _FileSnapshot) -> None:
        if snapshot.content is None:
            path.unlink(missing_ok=True)
            return
        write_private_bytes(path, snapshot.content)

    @staticmethod
    def _token_files(index: _AccountIndex) -> set[str]:
        return {
            authorization.token_file
            for account in index.accounts
            for authorization in account.authorizations.values()
        }

    def snapshot(self) -> GoogleAccountStoreSnapshot:
        with self._lock:
            index = self._read_index()
            return GoogleAccountStoreSnapshot(
                index=self._snapshot(self.index_path),
                tokens=tuple(
                    (token_file, self._snapshot(self.root / token_file))
                    for token_file in sorted(self._token_files(index))
                ),
            )

    def restore(self, snapshot: GoogleAccountStoreSnapshot) -> None:
        with self._lock:
            current_files = self._token_files(self._read_index())
            saved_tokens = dict(snapshot.tokens)
            for token_file in sorted(current_files | set(saved_tokens)):
                self._restore(
                    self.root / token_file,
                    saved_tokens.get(token_file, _FileSnapshot(content=None)),
                )
            self._restore(self.index_path, snapshot.index)

    @staticmethod
    def _replace_account(index: _AccountIndex, position: int, record: _AccountRecord) -> _AccountIndex:
        accounts = list(index.accounts)
        accounts[position] = record
        return _AccountIndex(version=2, accounts=accounts)

    def upsert_authorization(
        self,
        *,
        email: str,
        credential_json: str,
        scopes: tuple[str, ...],
        service: GoogleService,
        account_id: str | None = None,
    ) -> GoogleAccount:
        canonical_scopes = sorted(scopes)

        with self._lock:
            index = self._read_index()
            position = None
            if account_id is not None:
                position = next((i for i, account in enumerate(index.accounts) if account.id == account_id), None)
                if position is None:
                    raise KeyError(f"Unknown Google account: {account_id}")
            else:
                normalized_email = email.casefold()
                position = next(
                    (
                        i
                        for i, account in enumerate(index.accounts)
                        if account.email.casefold() == normalized_email
                    ),
                    None,
                )

            if position is None:
                record = _AccountRecord(
                    id=uuid4().hex,
                    email=email,
                    authorizations={},
                )
                position = len(index.accounts)
                index = _AccountIndex(version=2, accounts=[*index.accounts, record])
            else:
                record = index.accounts[position]

            authorizations = dict(record.authorizations)
            current = authorizations.get(service)
            shared = current is not None and any(
                other_service != service and authorization.token_file == current.token_file
                for other_service, authorization in authorizations.items()
            )
            token_file = (
                current.token_file
                if current is not None and not shared
                else f"google_tokens/{record.id}-{service}.json"
            )
            authorizations[service] = _AuthorizationRecord(
                token_file=token_file,
                scopes=canonical_scopes,
            )
            updated_record = _AccountRecord(
                id=record.id,
                email=email,
                authorizations=authorizations,
            )
            updated_index = self._replace_account(index, position, updated_record)
            token_path = self.root / token_file
            snapshot = self._snapshot(token_path)
            write_private_text(token_path, credential_json)
            try:
                self._write_index(updated_index)
            except BaseException as error:
                try:
                    self._restore(token_path, snapshot)
                except BaseException as rollback_error:
                    raise BaseExceptionGroup(
                        "Google account index write and credential rollback both failed",
                        [error, rollback_error],
                    ) from error
                raise
            return self._account(updated_record)

    def disconnect_service(self, account_id: str, service: GoogleService) -> GoogleAccount:
        with self._lock:
            index = self._read_index()
            position = next((i for i, account in enumerate(index.accounts) if account.id == account_id), None)
            if position is None:
                raise KeyError(f"Unknown Google account: {account_id}")
            record = index.accounts[position]
            authorization = record.authorizations.get(service)
            if authorization is None:
                raise KeyError(f"Google account {account_id} is not authorized for {service}")
            authorizations = dict(record.authorizations)
            del authorizations[service]
            updated_record = _AccountRecord(
                id=record.id,
                email=record.email,
                authorizations=authorizations,
            )
            updated_index = self._replace_account(index, position, updated_record)
            token_is_shared = any(
                candidate.token_file == authorization.token_file
                for account in updated_index.accounts
                for candidate in account.authorizations.values()
            )
            token_path = self.root / authorization.token_file
            snapshot = self._snapshot(token_path)
            try:
                if not token_is_shared:
                    token_path.unlink(missing_ok=True)
                self._write_index(updated_index)
            except BaseException as error:
                try:
                    self._restore(token_path, snapshot)
                except BaseException as rollback_error:
                    raise BaseExceptionGroup(
                        "Google service disconnect and credential rollback both failed",
                        [error, rollback_error],
                    ) from error
                raise
            return self._account(updated_record)

    def remove_account(self, account_id: str) -> GoogleAccount:
        with self._lock:
            index = self._read_index()
            position = next((i for i, account in enumerate(index.accounts) if account.id == account_id), None)
            if position is None:
                raise KeyError(f"Unknown Google account: {account_id}")
            record = index.accounts[position]
            remaining_accounts = [account for i, account in enumerate(index.accounts) if i != position]
            updated_index = _AccountIndex(version=2, accounts=remaining_accounts)
            remaining_token_files = {
                authorization.token_file
                for account in remaining_accounts
                for authorization in account.authorizations.values()
            }
            token_paths = [
                self.root / token_file
                for token_file in sorted(
                    {
                        authorization.token_file
                        for authorization in record.authorizations.values()
                        if authorization.token_file not in remaining_token_files
                    }
                )
            ]
            snapshots = {path: self._snapshot(path) for path in token_paths}
            try:
                for path in token_paths:
                    path.unlink(missing_ok=True)
                self._write_index(updated_index)
            except BaseException as error:
                rollback_errors: list[BaseException] = []
                for path, snapshot in snapshots.items():
                    try:
                        self._restore(path, snapshot)
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    raise BaseExceptionGroup(
                        "Google account removal and credential rollback failed",
                        [error, *rollback_errors],
                    ) from error
                raise
            return self._account(record)
