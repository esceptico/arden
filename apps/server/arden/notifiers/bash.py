import asyncio
import os
from typing import Self

from arden.notifiers.base import Notifier, NotifierContext


class BashNotifier(Notifier):
    channel = "bash"

    @classmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self:
        return cls(command=config["command"])

    def __init__(self, command: str):
        self._command = command

    async def send(self, subject: str, body: str) -> None:
        env = {**os.environ, "ARDEN_SUBJECT": subject, "ARDEN_BODY": body}
        proc = await asyncio.create_subprocess_shell(
            self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, stderr = await proc.communicate(input=body.encode())
        if proc.returncode != 0:
            raise RuntimeError(f"Bash notifier {self._command!r} exited {proc.returncode}: {stderr.decode().strip()}")
