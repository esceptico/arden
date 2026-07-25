import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.integrations.google_drive.client import MultiGoogleDriveClient
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

CellValue = str | int | float | bool | None


def _drive(execution: ToolExecution) -> MultiGoogleDriveClient:
    return execution.ctx.get_client("google_drive", MultiGoogleDriveClient)


def _source_ref(data: dict, kind: Literal["document", "spreadsheet"]):
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="google_drive",
                kind=kind,
                ref=data["ref"],
                title=data.get("title") or data["ref"],
                url=data.get("url"),
            ),
        )
    )


class SearchGoogleDriveInput(BaseModel):
    query: str = Field(default="", max_length=500)
    kind: Literal["all", "doc", "sheet"] = "all"
    account: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


async def search_google_drive(execution: ToolExecution, args: SearchGoogleDriveInput) -> ToolResult:
    items = _drive(execution).search(args.query, kind=args.kind, account_id=args.account, limit=args.limit)
    if not items:
        return ToolResult(content="No matching Google Docs or Sheets", preview="0 files")
    lines = []
    for item in items:
        modified = ""
        if item.modified_time:
            modified = (
                f" · modified {format_timestamp(datetime.fromisoformat(item.modified_time.replace('Z', '+00:00')))}"
            )
        lines.append(f"• {item.name} ({item.kind}) id: {item.ref}{modified}")
    may_have_more = len(items) == args.limit
    if may_have_more:
        lines.append(f"Showing {args.limit} files; more may exist. Narrow the query to continue.")
    refs = normalize_source_refs(
        ToolSourceRef(
            provider="google_drive",
            kind=item.kind,
            ref=item.ref,
            title=item.name,
            url=item.url,
        )
        for item in items
    )
    return ToolResult(
        content="\n".join(lines),
        preview=f"{len(items)} files" + (" (possibly capped)" if may_have_more else ""),
        data={"count": len(items), "may_have_more": may_have_more},
        source_refs=refs,
    )


class ReadGoogleDocInput(BaseModel):
    document_ref: str = Field(min_length=1, max_length=500)


async def read_google_doc(execution: ToolExecution, args: ReadGoogleDocInput) -> ToolResult:
    client, document_id = _drive(execution).resolve_ref(args.document_ref)
    data = client.read_doc(document_id)
    return ToolResult(
        content=data["text"],
        preview=data["title"],
        source_refs=_source_ref(data, "document"),
    )


class ReadGoogleSheetInput(BaseModel):
    spreadsheet_ref: str = Field(min_length=1, max_length=500)
    range: str = Field(default="A1:Z200", min_length=1, max_length=200)


async def read_google_sheet(execution: ToolExecution, args: ReadGoogleSheetInput) -> ToolResult:
    client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
    data = client.read_sheet(spreadsheet_id, args.range)
    data["title"] = f"Sheet {data['range']}"
    rows = data["values"]
    table = "\n".join(" | ".join("" if value is None else str(value) for value in row) for row in rows)
    return ToolResult(
        content=f"Range: {data['range']}\n{table or '(empty)'}",
        preview=f"Read {data['range']}",
        data={"range": data["range"], "values": rows, "row_count": len(rows)},
        source_refs=_source_ref(data, "spreadsheet"),
    )


class CreateGoogleDocInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    account: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


async def create_google_doc(execution: ToolExecution, args: CreateGoogleDocInput) -> ToolResult:
    async def invoke() -> ToolResult:
        data = _drive(execution).select_account(args.account).create_doc(args.title)
        return mutation_result(
            content=f"Created {data['title']}: {data['url']}\nref: {data['ref']}",
            preview="Created document",
            operation="create",
            target=args.title,
            receipt=data["ref"],
            after_ref=data["ref"],
            observed=f"Drive returned {data['ref']}",
            data={"file_ref": data["ref"], "url": data["url"]},
        )

    return await execute_idempotent(
        execution,
        namespace=f"drive:create_doc:{args.account or 'default'}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_create_google_doc(_execution: ToolExecution, args: CreateGoogleDocInput) -> ApprovalInfo:
    return ApprovalInfo(description=args.title, preview="Create empty document", diff=None)


class EditGoogleDocInput(BaseModel):
    document_ref: str = Field(min_length=1, max_length=500)
    operation: Literal["append", "replace_all"]
    text: str = Field(max_length=100_000)
    match: str | None = Field(default=None, max_length=10_000)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def require_match(self):
        if self.operation == "replace_all" and not self.match:
            raise ValueError("replace_all requires match")
        return self


async def edit_google_doc(execution: ToolExecution, args: EditGoogleDocInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, document_id = _drive(execution).resolve_ref(args.document_ref)
        data = client.edit_doc(document_id, operation=args.operation, text=args.text, match=args.match)
        return mutation_result(
            content=f"Updated {data['title']}: {data['url']}",
            preview="Updated document",
            operation=args.operation,
            target=args.document_ref,
            receipt=args.idempotency_key,
            before_ref=args.document_ref,
            after_ref=data["ref"],
            observed=f"Drive returned {data['ref']}",
        )

    return await execute_idempotent(
        execution,
        namespace="drive:edit_doc",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_edit_google_doc(_execution: ToolExecution, args: EditGoogleDocInput) -> ApprovalInfo:
    change = f"Append:\n{args.text}" if args.operation == "append" else f"Replace {args.match!r} with:\n{args.text}"
    return ApprovalInfo(description=args.document_ref, preview=change[:1500], diff=None)


class CreateGoogleSheetInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    account: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


async def create_google_sheet(execution: ToolExecution, args: CreateGoogleSheetInput) -> ToolResult:
    async def invoke() -> ToolResult:
        data = _drive(execution).select_account(args.account).create_sheet(args.title)
        return mutation_result(
            content=f"Created {data['title']}: {data['url']}\nref: {data['ref']}",
            preview="Created spreadsheet",
            operation="create",
            target=args.title,
            receipt=data["ref"],
            after_ref=data["ref"],
            observed=f"Drive returned {data['ref']}",
            data={"file_ref": data["ref"], "url": data["url"]},
        )

    return await execute_idempotent(
        execution,
        namespace=f"drive:create_sheet:{args.account or 'default'}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_create_google_sheet(_execution: ToolExecution, args: CreateGoogleSheetInput) -> ApprovalInfo:
    return ApprovalInfo(description=args.title, preview="Create empty spreadsheet", diff=None)


class SheetWriteInput(BaseModel):
    spreadsheet_ref: str = Field(min_length=1, max_length=500)
    range: str = Field(min_length=1, max_length=200)
    values: list[list[CellValue]] = Field(min_length=1, max_length=500)
    value_input_option: Literal["RAW", "USER_ENTERED"] = "USER_ENTERED"
    idempotency_key: str = Field(min_length=8, max_length=200)


async def update_google_sheet(execution: ToolExecution, args: SheetWriteInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
        receipt = client.update_sheet(spreadsheet_id, args.range, args.values, args.value_input_option)
        return mutation_result(
            content=f"Updated {args.spreadsheet_ref} range {args.range}",
            preview="Updated cells",
            operation="update",
            target=f"{args.spreadsheet_ref}:{args.range}",
            receipt=json.dumps(receipt, sort_keys=True),
            before_ref=args.spreadsheet_ref,
            after_ref=args.spreadsheet_ref,
            observed=f"Drive acknowledged {receipt.get('updatedRange', args.range)}",
        )

    return await execute_idempotent(
        execution,
        namespace="drive:update_sheet",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def append_google_sheet_rows(execution: ToolExecution, args: SheetWriteInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
        receipt = client.append_sheet_rows(spreadsheet_id, args.range, args.values, args.value_input_option)
        return mutation_result(
            content=f"Appended rows to {args.spreadsheet_ref} range {args.range}",
            preview="Appended rows",
            operation="append",
            target=f"{args.spreadsheet_ref}:{args.range}",
            receipt=json.dumps(receipt, sort_keys=True),
            before_ref=args.spreadsheet_ref,
            after_ref=args.spreadsheet_ref,
            observed=f"Drive acknowledged append to {args.range}",
        )

    return await execute_idempotent(
        execution,
        namespace="drive:append_sheet",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_sheet_write(_execution: ToolExecution, args: SheetWriteInput) -> ApprovalInfo:
    return ApprovalInfo(
        description=f"{args.spreadsheet_ref} · {args.range}",
        preview=json.dumps(args.values[:20], ensure_ascii=False),
        diff=None,
    )


def _policy(action: ToolAction, *, approval: bool = False) -> ToolPolicy:
    return ToolPolicy(
        action=action,
        scope=ToolScope.EXTERNAL,
        requires_approval=approval,
        permissions=frozenset({"google_drive"}),
    )


search_google_drive_tool = tool(
    display_name="Search Google Drive",
    description="Search connected Google Docs and Sheets.",
    input_model=SearchGoogleDriveInput,
    policy=_policy(ToolAction.READ),
    execute=search_google_drive,
)
read_google_doc_tool = tool(
    display_name="Read Google Doc",
    description="Read a Google Doc by qualified reference.",
    input_model=ReadGoogleDocInput,
    policy=_policy(ToolAction.READ),
    execute=read_google_doc,
)
read_google_sheet_tool = tool(
    display_name="Read Google Sheet",
    description="Read a bounded A1 range from a Google Sheet.",
    input_model=ReadGoogleSheetInput,
    policy=_policy(ToolAction.READ),
    execute=read_google_sheet,
)
create_google_doc_tool = tool(
    display_name="Create Google Doc",
    display_description="Create an empty Google Doc.",
    description="Create an empty Google Doc. Use edit_google_doc in a separate operation to add content.",
    input_model=CreateGoogleDocInput,
    policy=_policy(ToolAction.WRITE, approval=True),
    approval=approve_create_google_doc,
    execute=create_google_doc,
)
edit_google_doc_tool = tool(
    display_name="Edit Google Doc",
    description="Append to or replace exact text in a Google Doc.",
    input_model=EditGoogleDocInput,
    policy=_policy(ToolAction.WRITE, approval=True),
    approval=approve_edit_google_doc,
    execute=edit_google_doc,
)
create_google_sheet_tool = tool(
    display_name="Create Google Sheet",
    display_description="Create an empty Google Sheet.",
    description="Create an empty Google Sheet. Use update_google_sheet in a separate operation to add values.",
    input_model=CreateGoogleSheetInput,
    policy=_policy(ToolAction.WRITE, approval=True),
    approval=approve_create_google_sheet,
    execute=create_google_sheet,
)
update_google_sheet_tool = tool(
    display_name="Update Google Sheet",
    description="Replace values in one exact A1 range.",
    input_model=SheetWriteInput,
    policy=_policy(ToolAction.WRITE, approval=True),
    approval=approve_sheet_write,
    execute=update_google_sheet,
)
append_google_sheet_rows_tool = tool(
    display_name="Append Google Sheet Rows",
    description="Append rows to a Google Sheet range.",
    input_model=SheetWriteInput,
    policy=_policy(ToolAction.WRITE, approval=True),
    approval=approve_sheet_write,
    execute=append_google_sheet_rows,
)

DRIVE_TOOLS = {
    "search_google_drive": search_google_drive_tool,
    "read_google_doc": read_google_doc_tool,
    "read_google_sheet": read_google_sheet_tool,
    "create_google_doc": create_google_doc_tool,
    "edit_google_doc": edit_google_doc_tool,
    "create_google_sheet": create_google_sheet_tool,
    "update_google_sheet": update_google_sheet_tool,
    "append_google_sheet_rows": append_google_sheet_rows_tool,
}
