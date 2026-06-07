"""Buyback config + pricing rule use cases (ADR-0007). Manager gating is enforced
at the interface; these enforce existence/uniqueness/target rules and own the commit.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.corporations import get_registered_corporation
from app.application.errors import (
    PricingRuleAlreadyExists,
    PricingRuleNotFound,
    PricingRuleTargetInvalid,
)
from app.config import get_settings
from app.data.records import BuybackConfigRecord, PricingRuleRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import sde as sde_repo
from app.domain.ids import generate_rule_id
from app.domain.pricing import (
    DEFAULT_AGGREGATE_FIELD,
    DEFAULT_BASIS,
    DEFAULT_PERCENTAGE,
    AggregateField,
    Basis,
    TargetKind,
)


async def get_config(
    session: AsyncSession, corporation_id: int
) -> BuybackConfigRecord:
    """Return the corp's config (404 if the corp isn't registered). Lazily creates
    the default config if missing — registration normally creates it."""
    await get_registered_corporation(session, corporation_id)
    config = await config_repo.get_config(session, corporation_id)
    if config is None:
        config = await config_repo.upsert_config(
            session,
            corporation_id=corporation_id,
            market_hub_id=get_settings().market_hub_id,
            default_basis=DEFAULT_BASIS,
            default_percentage=DEFAULT_PERCENTAGE,
            aggregate_field=DEFAULT_AGGREGATE_FIELD,
        )
        await session.commit()
    return config


async def update_config(
    session: AsyncSession,
    corporation_id: int,
    *,
    market_hub_id: int,
    default_basis: Basis,
    default_percentage: Decimal,
    aggregate_field: AggregateField,
) -> BuybackConfigRecord:
    await get_registered_corporation(session, corporation_id)
    config = await config_repo.upsert_config(
        session,
        corporation_id=corporation_id,
        market_hub_id=market_hub_id,
        default_basis=default_basis,
        default_percentage=default_percentage,
        aggregate_field=aggregate_field,
    )
    await session.commit()
    return config


async def list_rules(
    session: AsyncSession, corporation_id: int
) -> list[PricingRuleRecord]:
    await get_registered_corporation(session, corporation_id)
    return await rules_repo.list_rules(session, corporation_id)


async def create_rule(
    session: AsyncSession,
    *,
    corporation_id: int,
    target_kind: TargetKind,
    target_id: int,
    basis: Basis | None,
    percentage: Decimal,
    enabled: bool,
) -> PricingRuleRecord:
    await get_registered_corporation(session, corporation_id)
    await _validate_target(session, target_kind, target_id)
    if await rules_repo.get_rule_for_target(
        session,
        corporation_id=corporation_id,
        target_kind=target_kind,
        target_id=target_id,
    ):
        raise PricingRuleAlreadyExists()
    rule = await rules_repo.create_rule(
        session,
        public_id=generate_rule_id(),
        corporation_id=corporation_id,
        target_kind=target_kind,
        target_id=target_id,
        basis=basis,
        percentage=percentage,
        enabled=enabled,
    )
    await session.commit()
    return rule


async def update_rule(
    session: AsyncSession,
    *,
    corporation_id: int,
    public_id: str,
    fields: dict,
) -> PricingRuleRecord:
    """Patch `basis`/`percentage`/`enabled` on a rule. Target is immutable — change
    it by deleting and recreating."""
    await get_registered_corporation(session, corporation_id)
    existing = await rules_repo.get_rule(session, public_id)
    if existing is None or existing.corporation_id != corporation_id:
        raise PricingRuleNotFound()
    rule = await rules_repo.update_rule(session, public_id, **fields)
    await session.commit()
    return rule  # type: ignore[return-value]  # existence checked above


async def delete_rule(
    session: AsyncSession, *, corporation_id: int, public_id: str
) -> None:
    await get_registered_corporation(session, corporation_id)
    existing = await rules_repo.get_rule(session, public_id)
    if existing is None or existing.corporation_id != corporation_id:
        raise PricingRuleNotFound()
    await rules_repo.delete_rule(session, public_id)
    await session.commit()


async def _validate_target(
    session: AsyncSession, target_kind: TargetKind, target_id: int
) -> None:
    if target_kind == "type":
        if await sde_repo.get_type(session, target_id) is None:
            raise PricingRuleTargetInvalid(f"Unknown type {target_id}")
    elif await sde_repo.get_market_group(session, target_id) is None:
        raise PricingRuleTargetInvalid(f"Unknown market group {target_id}")
