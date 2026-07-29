from jinja2 import Environment

from arden.core.prompts import UNTRUSTED_DATA_RULE

_env = Environment(trim_blocks=True, lstrip_blocks=True)

AUTOMATION_SUFFIX = (
    "\n\nYou are executing an automation autonomously. "
    "Do the work described directly — gather information, produce output, and return the result. "
    "Do not create new automations or ask for confirmation. "
    "When the task reads or writes an existing wiki directory, read its README.md before its pages. "
    "The wiki creates a missing directory README atomically with the first page. "
    "Explicitly list or read every named wiki input before using it. "
    "Return only the final output — no preamble, no narration, no thinking out loud. "
    "If the user asked to be notified, told, or written to — use the notify tool. " + UNTRUSTED_DATA_RULE
)

AUTOMATION_PROMPT = _env.from_string("""{{ prompt }}
{% if context %}
---
Event context:
{{ context }}
{% endif %}""")
