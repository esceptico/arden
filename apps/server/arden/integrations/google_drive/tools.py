import json

from arden.agent.types.tools import ToolSourceRef, canonical_source_title, normalize_source_refs
from arden.integrations.google_drive.client import MultiGoogleDriveClient
from arden.integrations.google_drive.inputs import (
    DriveCreateDocInput,
    DriveCreateSheetInput,
    DriveEditDocInput,
    DriveReadDocInput,
    DriveReadSheetInput,
    DriveSearchInput,
    SheetWriteInput,
)
from arden.integrations.google_drive.models import DriveFile
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope


def _drive(execution: ToolExecution) -> MultiGoogleDriveClient:
    return execution.ctx.get_client("google_drive", MultiGoogleDriveClient)


def _source_ref(file: DriveFile):
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="google_drive",
                kind=file.kind,
                ref=file.ref,
                title=canonical_source_title(file.name, fallback=file.ref),
                url=file.url,
            ),
        )
    )


async def drive_search(execution: ToolExecution, args: DriveSearchInput) -> ToolResult:
    page = _drive(execution).search(
        args.query,
        kind=args.kind,
        account_ref=args.account,
        limit=args.limit,
        page_ref=args.page_ref,
    )
    items = page.files
    if not items:
        continuation = ""
        if page.next_page_ref is not None:
            continuation = f"\nMore files exist. Continue with page_ref: {page.next_page_ref.encode()}"
        return ToolResult(
            content="No matching Google Docs or Sheets" + continuation,
            preview="0 files" + (" (more available)" if page.has_more else ""),
            data={
                "count": 0,
                "next_page_ref": page.next_page_ref.encode() if page.next_page_ref is not None else None,
                "incomplete_search": page.incomplete_search,
            },
        )
    lines = []
    for item in items:
        modified = ""
        if item.modified_at is not None:
            modified = f" · modified {format_timestamp(item.modified_at)}"
        lines.append(f"• {item.name} ({item.kind}) ref: {item.ref}{modified}")
    may_have_more = page.has_more
    if may_have_more:
        next_page_ref = page.next_page_ref.encode()
        lines.append(f"More files exist. Continue with page_ref: {next_page_ref}")
    if page.incomplete_search:
        lines.append("Google reported an incomplete search. Select one account or narrow the query.")
    refs = normalize_source_refs(
        ToolSourceRef(
            provider="google_drive",
            kind=item.kind,
            ref=item.ref,
            title=canonical_source_title(item.name, fallback=item.ref),
            url=item.url,
        )
        for item in items
    )
    return ToolResult(
        content="\n".join(lines),
        preview=f"{len(items)} files" + (" (possibly capped)" if may_have_more else ""),
        data={
            "count": len(items),
            "next_page_ref": page.next_page_ref.encode() if page.next_page_ref is not None else None,
            "incomplete_search": page.incomplete_search,
        },
        source_refs=refs,
    )


async def drive_read_doc(execution: ToolExecution, args: DriveReadDocInput) -> ToolResult:
    client, document_id = _drive(execution).resolve_ref(args.document_ref)
    document = client.read_doc(document_id)
    return ToolResult(
        content=document.text,
        preview=document.file.name,
        source_refs=_source_ref(document.file),
    )


async def drive_read_sheet(execution: ToolExecution, args: DriveReadSheetInput) -> ToolResult:
    client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
    sheet_range = client.read_sheet(spreadsheet_id, args.range)
    rows = [list(row) for row in sheet_range.values]
    table = "\n".join(" | ".join("" if value is None else str(value) for value in row) for row in rows)
    return ToolResult(
        content=f"Range: {sheet_range.range_name}\n{table or '(empty)'}",
        preview=f"Read {sheet_range.range_name}",
        data={"range": sheet_range.range_name, "values": rows, "row_count": len(rows)},
        source_refs=_source_ref(sheet_range.file),
    )


async def drive_create_doc(execution: ToolExecution, args: DriveCreateDocInput) -> ToolResult:
    client = _drive(execution).select_account(args.account)

    async def invoke() -> ToolResult:
        document = client.create_doc(args.title)
        return mutation_result(
            content=f"Created {document.name}: {document.url}\nref: {document.ref}",
            preview="Created document",
            operation="create",
            target=args.title,
            receipt=document.ref,
            after_ref=document.ref,
            observed=f"Drive returned {document.ref}",
            data={"file_ref": document.ref, "url": document.url},
        )

    return await execute_idempotent(
        execution,
        namespace=f"drive:create_doc:{client.account_ref}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_drive_create_doc(_execution: ToolExecution, args: DriveCreateDocInput) -> ApprovalInfo:
    return ApprovalInfo(description=args.title, preview="Create empty document", diff=None)


async def drive_edit_doc(execution: ToolExecution, args: DriveEditDocInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, document_id = _drive(execution).resolve_ref(args.document_ref)
        document = client.edit_doc(document_id, operation=args.operation, text=args.text, match=args.match)
        return mutation_result(
            content=f"Updated {document.name}: {document.url}",
            preview="Updated document",
            operation=args.operation,
            target=args.document_ref,
            receipt=args.idempotency_key,
            before_ref=args.document_ref,
            after_ref=document.ref,
            observed=f"Drive returned {document.ref}",
        )

    return await execute_idempotent(
        execution,
        namespace="drive:edit_doc",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_drive_edit_doc(_execution: ToolExecution, args: DriveEditDocInput) -> ApprovalInfo:
    change = f"Append:\n{args.text}" if args.operation == "append" else f"Replace {args.match!r} with:\n{args.text}"
    return ApprovalInfo(description=args.document_ref, preview=change[:1500], diff=None)


async def drive_create_sheet(execution: ToolExecution, args: DriveCreateSheetInput) -> ToolResult:
    client = _drive(execution).select_account(args.account)

    async def invoke() -> ToolResult:
        spreadsheet = client.create_sheet(args.title)
        return mutation_result(
            content=f"Created {spreadsheet.name}: {spreadsheet.url}\nref: {spreadsheet.ref}",
            preview="Created spreadsheet",
            operation="create",
            target=args.title,
            receipt=spreadsheet.ref,
            after_ref=spreadsheet.ref,
            observed=f"Drive returned {spreadsheet.ref}",
            data={"file_ref": spreadsheet.ref, "url": spreadsheet.url},
        )

    return await execute_idempotent(
        execution,
        namespace=f"drive:create_sheet:{client.account_ref}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_drive_create_sheet(_execution: ToolExecution, args: DriveCreateSheetInput) -> ApprovalInfo:
    return ApprovalInfo(description=args.title, preview="Create empty spreadsheet", diff=None)


async def drive_update_sheet(execution: ToolExecution, args: SheetWriteInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
        receipt = client.update_sheet(spreadsheet_id, args.range, args.values, args.value_input_option)
        return mutation_result(
            content=f"Updated {receipt.file_ref} range {args.range}",
            preview="Updated cells",
            operation="update",
            target=f"{args.spreadsheet_ref}:{args.range}",
            receipt=receipt.acknowledged_range,
            before_ref=args.spreadsheet_ref,
            after_ref=receipt.file_ref,
            observed=f"Drive acknowledged {receipt.acknowledged_range}",
        )

    return await execute_idempotent(
        execution,
        namespace="drive:update_sheet",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def drive_append_sheet_rows(execution: ToolExecution, args: SheetWriteInput) -> ToolResult:
    async def invoke() -> ToolResult:
        client, spreadsheet_id = _drive(execution).resolve_ref(args.spreadsheet_ref)
        receipt = client.append_sheet_rows(spreadsheet_id, args.range, args.values, args.value_input_option)
        return mutation_result(
            content=f"Appended rows to {receipt.file_ref} range {args.range}",
            preview="Appended rows",
            operation="append",
            target=f"{args.spreadsheet_ref}:{args.range}",
            receipt=receipt.acknowledged_range,
            before_ref=args.spreadsheet_ref,
            after_ref=receipt.file_ref,
            observed=f"Drive acknowledged append to {receipt.acknowledged_range}",
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


def _policy(
    action: ToolAction,
    *,
    approval: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
) -> ToolPolicy:
    return ToolPolicy(
        action=action,
        scope=ToolScope.EXTERNAL,
        requires_approval=approval,
        permissions=frozenset({"google_drive"}),
        deferred=True,
        destructive=destructive,
        open_world=True,
        idempotent=idempotent,
    )


drive_search_tool = tool(
    display_name="Search Google Drive",
    description=(
        "Search connected Google Docs and Sheets. Start with a query; optionally select one "
        "connected account. Results include account-qualified file refs. If the result says "
        "more files exist, call this tool again with only its exact page_ref; it preserves the "
        "original account, query, type, and limit. Read files before editing. All writes require "
        "approval preview and an idempotency_key; inspect the returned receipt to verify the change."
    ),
    input_model=DriveSearchInput,
    policy=_policy(ToolAction.READ),
    execute=drive_search,
)
drive_read_doc_tool = tool(
    display_name="Read Google Doc",
    description="Read a Google Doc by qualified reference.",
    input_model=DriveReadDocInput,
    policy=_policy(ToolAction.READ),
    execute=drive_read_doc,
)
drive_read_sheet_tool = tool(
    display_name="Read Google Sheet",
    description="Read a bounded A1 range from a Google Sheet.",
    input_model=DriveReadSheetInput,
    policy=_policy(ToolAction.READ),
    execute=drive_read_sheet,
)
drive_create_doc_tool = tool(
    display_name="Create Google Doc",
    display_description="Create an empty Google Doc.",
    description="Create an empty Google Doc. Use drive_edit_doc in a separate operation to add content.",
    input_model=DriveCreateDocInput,
    policy=_policy(ToolAction.WRITE, approval=True, destructive=False, idempotent=True),
    approval=approve_drive_create_doc,
    execute=drive_create_doc,
)
drive_edit_doc_tool = tool(
    display_name="Edit Google Doc",
    description="Append to or replace exact text in a Google Doc.",
    input_model=DriveEditDocInput,
    policy=_policy(ToolAction.WRITE, approval=True, destructive=True, idempotent=True),
    approval=approve_drive_edit_doc,
    execute=drive_edit_doc,
)
drive_create_sheet_tool = tool(
    display_name="Create Google Sheet",
    display_description="Create an empty Google Sheet.",
    description="Create an empty Google Sheet. Use drive_update_sheet in a separate operation to add values.",
    input_model=DriveCreateSheetInput,
    policy=_policy(ToolAction.WRITE, approval=True, destructive=False, idempotent=True),
    approval=approve_drive_create_sheet,
    execute=drive_create_sheet,
)
drive_update_sheet_tool = tool(
    display_name="Update Google Sheet",
    description="Replace values in one exact A1 range.",
    input_model=SheetWriteInput,
    policy=_policy(ToolAction.WRITE, approval=True, destructive=True, idempotent=True),
    approval=approve_sheet_write,
    execute=drive_update_sheet,
)
drive_append_sheet_rows_tool = tool(
    display_name="Append Google Sheet Rows",
    description="Append rows to a Google Sheet range.",
    input_model=SheetWriteInput,
    policy=_policy(ToolAction.WRITE, approval=True, destructive=False, idempotent=True),
    approval=approve_sheet_write,
    execute=drive_append_sheet_rows,
)

DRIVE_TOOLS = {
    "drive_search": drive_search_tool,
    "drive_read_doc": drive_read_doc_tool,
    "drive_read_sheet": drive_read_sheet_tool,
    "drive_create_doc": drive_create_doc_tool,
    "drive_edit_doc": drive_edit_doc_tool,
    "drive_create_sheet": drive_create_sheet_tool,
    "drive_update_sheet": drive_update_sheet_tool,
    "drive_append_sheet_rows": drive_append_sheet_rows_tool,
}
