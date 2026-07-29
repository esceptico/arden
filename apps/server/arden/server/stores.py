from typing import Self

import arden.database as database
from arden.areas.work_store import AreaWorkStore
from arden.automation.store import AutomationStore
from arden.config import Config
from arden.context.store import SessionStore
from arden.monitor.store import MonitorStateStore
from arden.notifiers.store import NotifierStore
from arden.outbox.store import OutboxStore
from arden.services.session import SessionService


class Stores:
    """Database connections and all stores sharing them.

    Uses three connections to sessions.db:
    - conn: for ordinary writes
    - automation_settlement_conn: isolated atomic run/outbox settlement
    - read_conn: for reads (concurrent with writes in WAL mode)
    """

    def __init__(
        self,
        conn: database.aiosqlite.Connection,
        automation_settlement_conn: database.aiosqlite.Connection,
        read_conn: database.aiosqlite.Connection,
        sessions: SessionService,
        automations: AutomationStore,
        notifiers: NotifierStore,
        monitor: MonitorStateStore,
        outbox: OutboxStore,
        area_work: AreaWorkStore,
    ):
        self.conn = conn
        self.automation_settlement_conn = automation_settlement_conn
        self.read_conn = read_conn
        self.sessions = sessions
        self.automations = automations
        self.notifiers = notifiers
        self.monitor = monitor
        self.outbox = outbox
        self.area_work = area_work

    @classmethod
    async def connect(cls, config: Config) -> Self:
        config.db_dir.mkdir(exist_ok=True)
        conn = await database.connect(config.sessions_db_path)
        automation_settlement_conn = await database.connect(config.sessions_db_path)
        read_conn = await database.connect(config.sessions_db_path, readonly=True)

        session_store = SessionStore(conn, read_conn)
        await session_store.init_schema()
        await session_store.mark_interrupted_chat_runs()
        await session_store.mark_interrupted_chat_queued_messages_retryable()
        await session_store.prune_expired_chat_idempotency_keys()
        await session_store.mark_interrupted_background_agent_runs()
        await session_store.mark_interrupted_agent_sessions()

        outbox = OutboxStore(conn)
        await outbox.init_schema()
        settlement_outbox = OutboxStore(automation_settlement_conn)

        automations = AutomationStore(
            conn,
            settlement_outbox,
            settlement_conn=automation_settlement_conn,
        )
        await automations.init_schema()

        area_work = AreaWorkStore(conn, read_conn)
        await area_work.init_schema()

        notifiers = NotifierStore(conn)
        await notifiers.init_schema()

        monitor = MonitorStateStore(conn)
        await monitor.init_schema()

        return cls(
            conn=conn,
            automation_settlement_conn=automation_settlement_conn,
            read_conn=read_conn,
            sessions=SessionService(session_store),
            automations=automations,
            notifiers=notifiers,
            monitor=monitor,
            outbox=outbox,
            area_work=area_work,
        )

    async def close(self) -> None:
        await self.read_conn.close()
        await self.automation_settlement_conn.close()
        await self.conn.close()
