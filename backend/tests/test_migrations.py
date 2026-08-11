"""Migration chain ↔ ORM metadata parity.

The rest of the suite builds its schema with `Base.metadata.create_all`, so a
migration defect that only manifests on a *migrated* database (e.g. a column left
narrower than the ORM now expects) would otherwise escape the tests entirely. This
test builds one database via `alembic upgrade head` and asserts that autogenerate
against it produces zero operations — i.e. the migration chain and the ORM models
describe the same schema.

Autogenerate can't diff CHECK constraints, so growing a `check_enum` Literal with a
value *shorter* than the current longest changes no column type yet still needs a
migration (the migrated DB's CHECK would reject the new value). The test therefore
also compares every check_enum CHECK constraint's allowed values against its Literal.
"""

import asyncio
import re
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Enum, inspect, text

from alembic import command
from app.data.db import Base, engine

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    # Absolute path so the test doesn't depend on pytest's working directory.
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return cfg


def _autogenerate_diffs(sync_conn):
    ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
    return compare_metadata(ctx, Base.metadata)


def _enum_columns():
    """(table name, column name, Enum type) for every check_enum column in the ORM."""
    out = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Enum):
                out.append((table.name, column.name, column.type))
    return out


def _db_enum_check_values(sync_conn):
    """Allowed values per check_enum column, parsed from the migrated CHECK constraints.

    check_enum names the CHECK after the Enum (e.g. "expense_kind"), so match on that.
    """
    inspector = inspect(sync_conn)
    found = {}
    for table_name, column_name, enum_type in _enum_columns():
        for check in inspector.get_check_constraints(table_name):
            if check["name"] == enum_type.name:
                found[(table_name, column_name)] = set(re.findall(r"'([^']*)'", check["sqltext"]))
    return found


async def _wipe_schema() -> None:
    # Drop everything, including alembic_version, which `Base.metadata.drop_all`
    # (the conftest fixture's teardown) doesn't know about.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


async def test_migrated_schema_matches_orm_metadata():
    await _wipe_schema()
    try:
        # alembic/env.py calls asyncio.run(), so the upgrade must run off this loop.
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        async with engine.connect() as conn:
            diffs = await conn.run_sync(_autogenerate_diffs)
            db_checks = await conn.run_sync(_db_enum_check_values)
    finally:
        await _wipe_schema()

    rendered = "\n".join(repr(d) for d in diffs)
    assert diffs == [], (
        "The migrated schema differs from Base.metadata — a model change is missing "
        f"a migration (or a migration is wrong):\n{rendered}"
    )

    for table_name, column_name, enum_type in _enum_columns():
        expected = set(enum_type.enums)
        actual = db_checks.get((table_name, column_name))
        assert actual == expected, (
            f"CHECK {enum_type.name!r} on {table_name}.{column_name} allows "
            f"{sorted(actual) if actual else actual}, but the domain Literal expects "
            f"{sorted(expected)} — the migration updating the closed set is missing or "
            "wrong (autogenerate can't produce CHECK changes; see a91c4f2b7d13 for the "
            "hand-written pattern)."
        )
