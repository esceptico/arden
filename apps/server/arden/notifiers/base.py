from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Self


@dataclass
class NotifierContext:
    get_source: Callable[[str], Any]
    get_config_value: Callable[[str], Any]


class Notifier(ABC):
    channel: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self: ...

    @abstractmethod
    async def send(self, subject: str, body: str) -> None: ...
