import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager


class ChannelQueue:
    def __init__(self):
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @asynccontextmanager
    async def lock_for(self, channel: str, native_thread_id: str) -> AsyncIterator[None]:
        key = (channel, native_thread_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            yield

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def gather(self, *aws: Awaitable):
        return await asyncio.gather(*aws)
