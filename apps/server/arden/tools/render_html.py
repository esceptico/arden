from typing import Literal

from pydantic import BaseModel, Field

from arden.agent import ToolResult
from arden.constants import RENDER_HTML_MAX_CHARS
from arden.tools.core import ToolAction, ToolPolicy, ToolScope, tool
from arden.tools.core.context import ToolExecution

RENDER_HTML_DESCRIPTION = """Render an HTML widget as a rich card in the chat.

The HTML must be fully self-contained: inline all CSS and JavaScript. The widget runs in a sandboxed iframe with a strict Content-Security-Policy — external scripts, stylesheets, images, fonts, fetch/XHR, form submissions to URLs, and link navigation are all blocked or swallowed; assume the widget has no network access. Images must be data: URIs. Do not include links or form actions pointing at external URLs — they will not work.

The host renders your HTML inside a card that already has a border, rounded corners, padding, and a background. If you pass a title, the card shows a header with it (otherwise the card is headerless) — do NOT draw your own outer container: no wrapper panel with border/border-radius/box-shadow/background, no repeating the title as a heading. Start directly with content (controls, table, chart, prose) and let it fill 100% of the available width.

Theming: the host injects the app's design tokens as CSS variables on :root — use them so the widget matches the app. Available: --color-bg, --color-surface, --color-surface-soft, --color-line, --color-line-soft, --color-ink, --color-ink-soft, --color-muted, --color-faint, --color-accent, --color-accent-soft, --color-ok, --color-warn, --color-bad, --font-sans, --font-mono. The body already has comfortable padding, the app font, and a transparent background.

A base stylesheet is provided — prefer plain semantic HTML over custom CSS: headings, p, a, table, code/pre, hr, label, input, select, textarea, button (button.primary for the main action) are all pre-styled to match the app. Utility classes: .field (label + control block), .grid-2 (two-column grid), .chip (bordered checkbox/radio label that highlights when checked), .actions (right-aligned footer row for buttons), .muted (secondary text). Write custom CSS only for bespoke visuals (charts, diagrams, custom layouts).

For forms, arden.submitForm(form) collects every named field (FormData semantics: checkbox groups sharing a name become arrays) and submits it — e.g. <form onsubmit="arden.submitForm(this); return false"> with named inputs needs no other JavaScript.

mode="display": returns immediately. Use for charts, tables, visualizations, and self-contained interactive content. Nothing the user does in the widget comes back to you.

mode="input": BLOCKS until the user responds (or times out after a few minutes). Build a form or controls and call arden.submit({...}) exactly once with a flat JSON object of the collected values, e.g. arden.submit({rating: 4, comment: "fine"}). Optionally include a dismiss control that calls arden.cancel(). The tool result is a JSON string {"action": "accept" | "decline" | "cancel", "values": {...}} — "accept" means the user submitted (read values), "decline" means the user explicitly declined, "cancel" means dismissed or timed out (values is empty for both). Requires an interactive client: in background agents and automation runs, mode="input" fails immediately — use mode="display" or plain text instead."""


class RenderHtmlInput(BaseModel):
    html: str = Field(
        max_length=RENDER_HTML_MAX_CHARS,
        description="Self-contained HTML to render inside the sandboxed widget. Inline CSS/JS only; no external resources.",
    )
    title: str = Field(
        default="",
        max_length=200,
        description="Optional short title shown on the widget card's header. Omit for a headerless card.",
    )
    mode: Literal["display", "input"] = Field(
        description='"display" renders and returns immediately; "input" blocks until the user responds via arden.submit()/arden.cancel().'
    )


async def render_html(execution: ToolExecution, args: RenderHtmlInput) -> ToolResult:
    data = {"html": args.html, "title": args.title, "mode": args.mode}
    label = args.title or "widget"
    if args.mode == "display":
        return ToolResult(content=f"Rendered HTML {label}.", preview=label, data=data)
    envelope = await execution.request_input(html=args.html, title=args.title)
    if envelope is None:
        return ToolResult.error(
            'No interactive client connected — render_html mode="input" requires an active desktop session. '
            'Use mode="display" or ask in plain text instead.'
        )
    return ToolResult(content=envelope, preview=label, data=data)


render_html_tool = tool(
    display_name="Render HTML",
    description=RENDER_HTML_DESCRIPTION,
    input_model=RenderHtmlInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False),
    execute=render_html,
    kind="html_widget",
)
