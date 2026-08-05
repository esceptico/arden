"""Strict LLM-facing inputs for Google Drive tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arden.integrations.google_auth.accounts import validate_google_account_email
from arden.integrations.google_drive.models import DriveCell, DrivePageRef, split_file_ref


def _exact_nonblank(value: str, field: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be exact non-blank text")
    return value


def _exact_account(value: str | None) -> str | None:
    if value is not None:
        validate_google_account_email(value)
    return value


class _DriveInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class DriveSearchInput(_DriveInput):
    query: str = Field(default="", max_length=500)
    kind: Literal["all", "doc", "sheet"] = "all"
    account: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        description="Exact Google account email returned by the connection flow.",
    )
    limit: int = Field(default=20, ge=1, le=100)
    page_ref: str | None = Field(
        default=None,
        max_length=8_000,
        description="Exact continuation returned by drive_search. Pass it alone, without other fields.",
    )

    @field_validator("query")
    @classmethod
    def exact_query(cls, value: str) -> str:
        if value and value != value.strip():
            raise ValueError("query must not have surrounding whitespace")
        return value

    @field_validator("account")
    @classmethod
    def exact_account(cls, value: str | None) -> str | None:
        return _exact_account(value)

    @model_validator(mode="after")
    def exact_page_ref(self):
        if self.page_ref is None:
            return self
        DrivePageRef.decode(self.page_ref)
        if self.model_fields_set != {"page_ref"}:
            raise ValueError("page_ref must be passed alone")
        return self


class DriveReadDocInput(_DriveInput):
    document_ref: str = Field(
        min_length=1,
        max_length=500,
        description="Exact account-qualified document ref returned by drive_search or drive_create_doc.",
    )

    @field_validator("document_ref")
    @classmethod
    def exact_document_ref(cls, value: str) -> str:
        split_file_ref(value)
        return value


class DriveReadSheetInput(_DriveInput):
    spreadsheet_ref: str = Field(
        min_length=1,
        max_length=500,
        description="Exact account-qualified spreadsheet ref returned by drive_search or drive_create_sheet.",
    )
    range: str = Field(default="A1:Z200", min_length=1, max_length=200)

    @field_validator("spreadsheet_ref")
    @classmethod
    def exact_spreadsheet_ref(cls, value: str) -> str:
        split_file_ref(value)
        return value

    @field_validator("range")
    @classmethod
    def exact_range(cls, value: str) -> str:
        return _exact_nonblank(value, "range")


class DriveCreateDocInput(_DriveInput):
    title: str = Field(min_length=1, max_length=300)
    account: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        description="Exact Google account email returned by the connection flow.",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("title")
    @classmethod
    def exact_title(cls, value: str) -> str:
        return _exact_nonblank(value, "title")

    @field_validator("account")
    @classmethod
    def exact_account(cls, value: str | None) -> str | None:
        return _exact_account(value)


class DriveEditDocInput(_DriveInput):
    document_ref: str = Field(
        min_length=1,
        max_length=500,
        description="Exact account-qualified document ref returned by a Drive tool.",
    )
    operation: Literal["append", "replace_all"]
    text: str = Field(max_length=100_000)
    match: str | None = Field(default=None, max_length=10_000)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("document_ref")
    @classmethod
    def exact_document_ref(cls, value: str) -> str:
        split_file_ref(value)
        return value

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation == "replace_all" and not self.match:
            raise ValueError("replace_all requires match")
        if self.operation == "append" and self.match is not None:
            raise ValueError("append does not accept match")
        if self.operation == "append" and not self.text:
            raise ValueError("append requires text")
        return self


class DriveCreateSheetInput(_DriveInput):
    title: str = Field(min_length=1, max_length=300)
    account: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        description="Exact Google account email returned by the connection flow.",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("title")
    @classmethod
    def exact_title(cls, value: str) -> str:
        return _exact_nonblank(value, "title")

    @field_validator("account")
    @classmethod
    def exact_account(cls, value: str | None) -> str | None:
        return _exact_account(value)


class SheetWriteInput(_DriveInput):
    spreadsheet_ref: str = Field(
        min_length=1,
        max_length=500,
        description="Exact account-qualified spreadsheet ref returned by a Drive tool.",
    )
    range: str = Field(min_length=1, max_length=200)
    values: list[list[DriveCell]] = Field(min_length=1, max_length=500)
    value_input_option: Literal["RAW", "USER_ENTERED"] = "USER_ENTERED"
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("spreadsheet_ref")
    @classmethod
    def exact_spreadsheet_ref(cls, value: str) -> str:
        split_file_ref(value)
        return value

    @field_validator("range")
    @classmethod
    def exact_range(cls, value: str) -> str:
        return _exact_nonblank(value, "range")

    @model_validator(mode="after")
    def bounded_values(self):
        if any(not row for row in self.values):
            raise ValueError("values must not contain empty rows")
        if sum(len(row) for row in self.values) > 100_000:
            raise ValueError("values exceed 100000 cells")
        return self
