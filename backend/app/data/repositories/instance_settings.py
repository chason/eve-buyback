"""Instance-setting persistence (ADR-0042): runtime-editable operations knobs.
Repositories never commit (data/ conventions)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import InstanceSetting


async def get_value(session: AsyncSession, key: str) -> str | None:
    row = (
        await session.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    ).scalar_one_or_none()
    return row.value if row else None


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    row = (
        await session.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        session.add(InstanceSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()
