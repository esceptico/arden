import re
import sys
from datetime import UTC, datetime

from arden.integrations import ALL_INTEGRATIONS
from arden.logging import get_logger
from arden.notifiers.base import Notifier, NotifierContext
from arden.notifiers.bash import BashNotifier
from arden.notifiers.models import NotifierConfig
from arden.notifiers.store import NotifierStore

_logger = get_logger(__name__)
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*$")

# Builtin notifiers not yet migrated to integrations
_BUILTIN_NOTIFIERS: list[type[Notifier]] = [BashNotifier]

_NOTIFIER_CLASSES: dict[str, type[Notifier]] = {
    cls.channel: cls
    for cls in [
        *_BUILTIN_NOTIFIERS,
        *(i.notifier_class for i in ALL_INTEGRATIONS if i.notifier_class is not None),
    ]
}

NOTIFIER_FIELDS: dict[str, list[str]] = {
    "email": ["from_account", "to_address"],
    "telegram": ["user_id"],
    "slack": ["channel"],
    "bash": ["command"],
}


class NotifierService:
    def __init__(self, store: NotifierStore, ctx: NotifierContext):
        self.store = store
        self._ctx = ctx
        self._notifiers: dict[str, Notifier] = {}

    @property
    def notifiers(self) -> dict[str, Notifier]:
        return self._notifiers

    async def rebuild(self) -> None:
        new_notifiers: dict[str, Notifier] = {}
        for cfg in await self.store.list_all():
            try:
                cls = _NOTIFIER_CLASSES.get(cfg.type)
                if not cls:
                    _logger.warning("Unknown notifier type %r for %r", cfg.type, cfg.name)
                    continue
                new_notifiers[cfg.name] = cls.from_config(cfg.config, self._ctx)
            except Exception:
                _logger.exception("Failed to create notifier %r", cfg.name)
        self._notifiers = new_notifiers

    async def seed_defaults(self) -> None:
        existing = await self.store.list_all()
        if existing:
            return
        if sys.platform == "darwin":
            await self.store.save(
                NotifierConfig(
                    name="macos-sound",
                    type="bash",
                    config={
                        "command": 'osascript -e \'display notification "Task completed" with title "Arden" sound name "Glass"\''
                    },
                    created_at=datetime.now(UTC),
                )
            )

    def list_names(self) -> list[str]:
        return list(self._notifiers.keys())

    def list_summary(self) -> list[dict[str, str]]:
        return [{"name": name, "type": n.channel} for name, n in self._notifiers.items()]

    def get_types(self) -> dict:
        gmail = self._ctx.get_source("gmail")
        types = {name: {"fields": fields} for name, fields in NOTIFIER_FIELDS.items()}
        types["email"]["accounts"] = gmail.list_accounts() if gmail else []
        return types

    async def list_configs(self) -> list[NotifierConfig]:
        return await self.store.list_all()

    def validate_config(self, notifier_type: str, config: dict) -> None:
        if (fields := NOTIFIER_FIELDS.get(notifier_type)) is None:
            raise ValueError(f"Invalid notifier type: {notifier_type}")
        for field in fields:
            if not config.get(field):
                raise ValueError(f"{field} is required")
        if notifier_type == "email":
            gmail = self._ctx.get_source("gmail")
            if gmail and config["from_account"] not in gmail.list_accounts():
                raise ValueError(f"Unknown Gmail account: {config['from_account']}")

    async def create(self, name: str, notifier_type: str, config: dict) -> NotifierConfig:
        if not NAME_RE.match(name):
            raise ValueError("Name must be alphanumeric with hyphens")

        existing = await self.store.get(name)
        if existing:
            raise ValueError(f"Notifier '{name}' already exists")

        self.validate_config(notifier_type, config)

        cfg = NotifierConfig(
            name=name,
            type=notifier_type,
            config=config,
            created_at=datetime.now(UTC),
        )
        await self.store.save(cfg)
        await self.rebuild()
        return cfg

    async def update(self, name: str, new_config: dict, new_name: str | None = None) -> NotifierConfig:
        existing = await self.store.get(name)
        if not existing:
            raise KeyError(f"Notifier '{name}' not found")

        self.validate_config(existing.type, new_config)

        if new_name and new_name != name:
            if not NAME_RE.match(new_name):
                raise ValueError("Name must be alphanumeric with hyphens")
            conflict = await self.store.get(new_name)
            if conflict:
                raise ValueError(f"Notifier '{new_name}' already exists")
            await self.store.rename(name, new_name, new_config)
            existing.name = new_name
            existing.config = new_config
        else:
            existing.config = new_config
            await self.store.save(existing)
        await self.rebuild()
        return existing

    async def delete(self, name: str) -> None:
        deleted = await self.store.delete(name)
        if not deleted:
            raise KeyError(f"Notifier '{name}' not found")

        await self.rebuild()

    async def test(self, name: str) -> None:
        notifier = self._notifiers.get(name)
        if not notifier:
            raise KeyError(f"Notifier '{name}' not found")

        await notifier.send("Hello from arden", "Test notification — if you see this, it works!")
