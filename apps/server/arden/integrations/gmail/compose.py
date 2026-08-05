import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

from arden.core.prompts import env
from arden.integrations.base import IntegrationOperationError

_EMAIL_HTML_TEMPLATE = env.from_string("""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
               line-height: 1.6; color: #333; max-width: 800px; margin: 0; padding: 20px; }
        table { border-collapse: collapse; width: 100%; margin: 16px 0; }
        th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
        th { background: #f5f5f5; font-weight: 600; }
        pre { background: #f5f5f5; padding: 12px; border-radius: 4px;
              overflow-x: auto; margin: 16px 0; }
        code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px;
               font-family: 'Courier New', Consolas, monospace; font-size: 0.9em; }
        pre code { background: transparent; padding: 0; }
        h1, h2, h3 { margin-top: 24px; margin-bottom: 12px; color: #111; font-weight: 600; }
        h1 { font-size: 24px; } h2 { font-size: 20px; } h3 { font-size: 18px; }
        ul, ol { margin: 12px 0; padding-left: 24px; } li { margin: 4px 0; }
        p { margin: 12px 0; } a { color: #0066cc; text-decoration: none; }
        a:hover { text-decoration: underline; } strong { font-weight: 600; }
        blockquote { border-left: 4px solid #ddd; margin: 16px 0;
                     padding-left: 16px; color: #666; }
    </style>
</head>
<body>
{{ content }}
</body>
</html>""")


def _encode(message: MIMEText | MIMEMultipart) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def has_header_line_break(value: str) -> bool:
    return "\r" in value or "\n" in value


def _validate_outbound_header(value: str, field: str) -> None:
    if has_header_line_break(value):
        raise IntegrationOperationError(
            code="invalid_input",
            safe_message=f"Email {field} must not contain line breaks.",
            retryable=False,
        )


def _validate_provider_header(value: str | None) -> None:
    if value is not None and has_header_line_break(value):
        raise IntegrationOperationError(
            code="invalid_provider_payload",
            safe_message="Gmail returned an invalid reply header.",
            retryable=False,
        )


def compose_email(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    html: bool,
) -> str:
    _validate_outbound_header(sender, "sender")
    _validate_outbound_header(recipient, "recipient")
    _validate_outbound_header(subject, "subject")
    if html:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain"))
        converted = markdown.Markdown(extensions=["extra", "nl2br", "sane_lists", "codehilite"]).convert(body)
        message.attach(MIMEText(_EMAIL_HTML_TEMPLATE.render(content=converted), "html"))
    else:
        message = MIMEText(body)
    message["To"] = recipient
    message["Subject"] = subject
    message["From"] = sender
    return _encode(message)


def compose_reply(
    *,
    sender: str,
    recipient: str,
    subject_header: str | None,
    body: str,
    message_id: str,
    references: str,
) -> str:
    _validate_outbound_header(sender, "sender")
    for value in (recipient, subject_header, message_id, references):
        _validate_provider_header(value)
    message = MIMEText(body)
    message["To"] = recipient
    if subject_header is not None:
        message["Subject"] = subject_header
    message["From"] = sender
    message["In-Reply-To"] = message_id
    message["References"] = f"{references} {message_id}".strip()
    return _encode(message)
