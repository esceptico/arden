import pytest

from arden.wiki.maintenance.store import (
    CONSUMER_ID,
    WikiMaintenanceStore,
    WikiMaintenanceWatermarkConflictError,
    WikiProjectionWatermarkConflictError,
)

pytestmark = pytest.mark.asyncio


def _revision(character: str) -> str:
    return character * 64


async def test_maintenance_watermark_survives_restart(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    store = await WikiMaintenanceStore.open(path)
    try:
        advanced = await store.advance(expected_revision=None, revision=_revision("a"))
        assert advanced.consumer_id == CONSUMER_ID
        assert advanced.revision == _revision("a")
    finally:
        await store.close()

    restored = await WikiMaintenanceStore.open(path)
    try:
        watermark = await restored.get_watermark()
        assert watermark is not None
        assert watermark.revision == _revision("a")
    finally:
        await restored.close()


async def test_maintenance_watermark_uses_compare_and_swap(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        await store.advance(expected_revision=None, revision=_revision("a"))
        with pytest.raises(WikiMaintenanceWatermarkConflictError):
            await store.advance(expected_revision=_revision("b"), revision=_revision("c"))
        await store.advance(expected_revision=_revision("a"), revision=_revision("b"))
        assert (await store.get_watermark()).revision == _revision("b")
    finally:
        await store.close()


async def test_projection_watermark_uses_its_own_compare_and_swap(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        await store.record_projection_revision(expected_revision=None, revision=_revision("a"))
        with pytest.raises(WikiProjectionWatermarkConflictError):
            await store.record_projection_revision(expected_revision=_revision("b"), revision=_revision("c"))
        await store.record_projection_revision(expected_revision=_revision("a"), revision=_revision("b"))
        assert await store.get_projection_revision() == _revision("b")
    finally:
        await store.close()
