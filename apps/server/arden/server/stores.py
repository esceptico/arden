from contextlib import AsyncExitStack
from typing import Self

import arden.database as database
from arden.areas.work_store import AreaWorkStore
from arden.automation.store import AutomationStore
from arden.config import Config
from arden.constants import EXECUTOR_LEASE_TTL_SECONDS
from arden.context.store import SessionStore
from arden.execution.commands import ExecutorCommandLog
from arden.execution.devices import ExecutorDeviceStore
from arden.execution.leases import LeaseStore
from arden.execution.store import InvocationStore
from arden.monitor.store import MonitorStateStore
from arden.notifiers.store import NotifierStore
from arden.outbox.store import OutboxStore
from arden.services.session import SessionService
from arden.skills.device_store import DeviceSkillStore


class Stores:
    """Database connections and all stores sharing them.

    Uses five connections to sessions.db:
    - conn: for ordinary writes
    - event_conn: isolated atomic event and tool-result-manifest writes
    - automation_settlement_conn: isolated atomic run/outbox settlement
    - chat_completion_conn: isolated atomic chat completion/outbox settlement
    - read_conn: for reads (concurrent with writes in WAL mode)
    """

    def __init__(
        self,
        write_coordinator: database.WriteCoordinator,
        conn: database.aiosqlite.Connection,
        event_conn: database.aiosqlite.Connection,
        automation_settlement_conn: database.aiosqlite.Connection,
        chat_completion_conn: database.aiosqlite.Connection,
        read_conn: database.aiosqlite.Connection,
        sessions: SessionService,
        automations: AutomationStore,
        notifiers: NotifierStore,
        monitor: MonitorStateStore,
        outbox: OutboxStore,
        area_work: AreaWorkStore,
        invocations: InvocationStore,
        executor_devices: ExecutorDeviceStore,
        executor_leases: LeaseStore,
        executor_commands: ExecutorCommandLog,
        device_skills: DeviceSkillStore,
    ):
        self.write_coordinator = write_coordinator
        self.conn = conn
        self.event_conn = event_conn
        self.automation_settlement_conn = automation_settlement_conn
        self.chat_completion_conn = chat_completion_conn
        self.read_conn = read_conn
        self.sessions = sessions
        self.automations = automations
        self.notifiers = notifiers
        self.monitor = monitor
        self.outbox = outbox
        self.area_work = area_work
        self.invocations = invocations
        self.executor_devices = executor_devices
        self.executor_leases = executor_leases
        self.executor_commands = executor_commands
        self.device_skills = device_skills

    @classmethod
    async def connect(cls, config: Config, *, defer_recovery: bool = False) -> Self:
        config.db_dir.mkdir(exist_ok=True)
        async with AsyncExitStack() as connections:
            write_coordinator = database.WriteCoordinator()
            conn = await database.connect(config.sessions_db_path, write_coordinator=write_coordinator)
            connections.push_async_callback(conn.close)
            event_conn = await database.connect(config.sessions_db_path, write_coordinator=write_coordinator)
            connections.push_async_callback(event_conn.close)
            automation_settlement_conn = await database.connect(
                config.sessions_db_path,
                write_coordinator=write_coordinator,
            )
            connections.push_async_callback(automation_settlement_conn.close)
            chat_completion_conn = await database.connect(
                config.sessions_db_path,
                write_coordinator=write_coordinator,
            )
            connections.push_async_callback(chat_completion_conn.close)
            read_conn = await database.connect(config.sessions_db_path, readonly=True)
            connections.push_async_callback(read_conn.close)

            session_store = SessionStore(conn, read_conn, chat_completion_conn, event_conn=event_conn)
            await session_store.init_schema()
            if not defer_recovery:
                await cls._reconcile_session_store(session_store)

            outbox = OutboxStore(conn, read_conn)
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

            invocations = InvocationStore(conn)
            await invocations.init_schema()

            executor_devices = ExecutorDeviceStore(conn)
            await executor_devices.init_schema()
            executor_leases = LeaseStore(conn, ttl_seconds=EXECUTOR_LEASE_TTL_SECONDS)
            await executor_leases.init_schema()
            executor_commands = ExecutorCommandLog(conn)
            await executor_commands.init_schema()
            device_skills = DeviceSkillStore(conn)
            await device_skills.init_schema()

            stores = cls(
                write_coordinator=write_coordinator,
                conn=conn,
                event_conn=event_conn,
                automation_settlement_conn=automation_settlement_conn,
                chat_completion_conn=chat_completion_conn,
                read_conn=read_conn,
                sessions=SessionService(
                    session_store,
                    cold_root=config.arden_dir / "cold-sessions",
                    blob_root=config.arden_dir / "blobs" / "tool-results",
                ),
                automations=automations,
                notifiers=notifiers,
                monitor=monitor,
                outbox=outbox,
                area_work=area_work,
                invocations=invocations,
                executor_devices=executor_devices,
                executor_leases=executor_leases,
                executor_commands=executor_commands,
                device_skills=device_skills,
            )
            connections.pop_all()
            return stores

    @staticmethod
    async def _reconcile_session_store(session_store: SessionStore) -> None:
        await session_store.ensure_startup_recovery_indexes()
        await session_store.events.reconcile_due_session_event_retention()
        await session_store.mark_interrupted_chat_runs()
        await session_store.release_interrupted_queued_receipts()
        await session_store.prune_expired_chat_idempotency_keys()
        await session_store.mark_interrupted_background_agent_runs()
        await session_store.mark_interrupted_agent_sessions()

    async def reconcile_interrupted_state(self) -> None:
        await self._reconcile_session_store(self.sessions.store)

    async def close(self) -> None:
        await self.read_conn.close()
        await self.chat_completion_conn.close()
        await self.automation_settlement_conn.close()
        await self.event_conn.close()
        await self.conn.close()
