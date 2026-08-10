import json
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import Request
from pydantic import BaseModel

from app.plugins.esi_common import ESI_BASE, scope_missing

# ESI's bulk name-resolution endpoint accepts up to 1000 ids per request.
_NAMES_CHUNK = 1000


class CorporationMembersForbidden(Exception):
    """ESI refused the corp member list (401/403): the authorizing character lacks
    permission to read it (the membership scope and/or the required in-game role,
    ADR-0036). Transport-level; the application layer maps it to a semantic error."""


class CorporationContractsForbidden(Exception):
    """ESI refused the corp contracts list/items (401/403): the corp ESI token lacks the
    contracts scope (an old grant predating ADR-0037) or the character lacks the in-game
    role. Transport-level; the watcher logs and skips without flagging the token failed."""


class CorporationWalletForbidden(Exception):
    """ESI refused the corp wallet transactions/journal (401/403): the corp ESI token
    lacks the corp-wallet scope (a grant predating ADR-0045) or the character lacks
    the Accountant/Junior Accountant role. Transport-level; the sales ingestion logs
    and skips without flagging the token failed."""


class CorporationOrdersForbidden(Exception):
    """ESI refused the corp market orders (401/403): missing orders scope (pre-ADR-0045
    grant) or in-game role. Transport-level; same skip semantics as the wallet."""


class CorporationAssetsForbidden(Exception):
    """ESI refused the corp assets list (401/403): the corp ESI token lacks the assets
    scope (a grant predating ADR-0044) or the character lacks the Director role.
    Transport-level; the hangar sync logs and skips without flagging the token failed."""


class OpenWindowForbidden(Exception):
    """ESI refused the open-window call (401/403): the login token lacks the
    `esi-ui.open_window.v1` scope (the character logged in before ADR-0038). Transport-level;
    the application maps it to a "log in again to enable Open in EVE" error."""


class CharacterWalletForbidden(Exception):
    """The token can't read the character's wallet (missing scope / revoked)."""


class WalletJournalEntry(BaseModel):
    """One entry from a character wallet journal (ADR-0042). Only the fields payment
    reconciliation needs; `reason` carries the player-entered transfer message where
    the payment reference lives. `amount` is signed (positive = incoming)."""

    id: int
    ref_type: str
    amount: Decimal | None = None
    first_party_id: int | None = None
    second_party_id: int | None = None
    reason: str | None = None
    date: datetime


class CorporationInfo(BaseModel):
    name: str
    ceo_id: int
    ticker: str | None = None


class CharacterInfo(BaseModel):
    name: str
    corporation_id: int


class CorporationContract(BaseModel):
    """A corp contract as returned by `/corporations/{id}/contracts/` (ADR-0037). Only the
    fields the watcher needs; `title` is the in-game Description (the member pastes the
    appraisal's public_id there). `price` is the ISK the corp pays to accept."""

    contract_id: int
    type: str
    status: str
    title: str | None = None
    price: Decimal = Decimal(0)
    start_location_id: int | None = None
    issuer_id: int | None = None
    # Which corp issued it, and whether on the corp's behalf (from corp hangars) —
    # together they identify OUTGOING corp sales (ADR-0045, #157).
    issuer_corporation_id: int | None = None
    for_corporation: bool = False
    acceptor_id: int | None = None
    date_issued: datetime
    date_completed: datetime | None = None
    date_expired: datetime | None = None


class CorpWalletTransaction(BaseModel):
    """One market fill from a corp wallet division (ADR-0045). `is_buy` False = a
    sale (the fills the ledger records); `transaction_id` is the idempotency key;
    `journal_ref_id` links the journal's tax entry back to this fill."""

    transaction_id: int
    date: datetime
    type_id: int
    quantity: int
    unit_price: Decimal
    is_buy: bool
    location_id: int
    journal_ref_id: int | None = None


class CorpJournalEntry(BaseModel):
    """One corp wallet-journal entry (ADR-0045). Only what the ingestion needs:
    `transaction_tax` and `brokers_fee` ref types; `context_id` carries the market
    transaction id for tax entries. `amount` is signed (fees/taxes negative)."""

    id: int
    ref_type: str
    amount: Decimal | None = None
    date: datetime
    context_id: int | None = None
    context_id_type: str | None = None


class CorpMarketOrder(BaseModel):
    """One corp market order (ADR-0045): the listed state (sell-order escrow has
    physically left the hangar) and the division guard (`wallet_division` is chosen
    per order by the trader)."""

    order_id: int
    type_id: int
    is_buy_order: bool = False
    price: Decimal
    volume_remain: int
    volume_total: int
    location_id: int
    wallet_division: int
    issued: datetime
    state: str | None = None  # set on history entries (e.g. cancelled/expired)


class CorporationDivisions(BaseModel):
    """The corp's custom division names from `/corporations/{id}/divisions/`
    (ADR-0048): division number → name, for wallets and hangars separately. Only
    divisions the corp has actually named appear — ESI omits `name` for the rest
    (including the unrenamable master wallet), and the UI falls back to generic
    labels. Empty when the token can't read them."""

    wallet: dict[int, str] = {}
    hangar: dict[int, str] = {}


class CorporationAsset(BaseModel):
    """One corp asset row from `/corporations/{id}/assets/` (ADR-0044). Only the fields
    the hangar reconciliation needs: `location_flag` is the hangar division
    (`CorpSAG1`…`CorpSAG7`) and `location_id` the station/structure — or, for items
    nested inside a container, the container's `item_id`."""

    item_id: int
    type_id: int
    quantity: int
    location_id: int
    location_flag: str
    is_singleton: bool = False


class ContractItem(BaseModel):
    type_id: int
    quantity: int
    # True = an item the issuer hands over (the buyback items); False = something the
    # contractor gives. For a buyback we only count the included items.
    is_included: bool = True


class EsiClient:
    """Minimal ESI client for public character/corporation lookups.

    A plugin (outside-API gateway): it speaks HTTP to EVE's ESI and always
    returns Pydantic models, never raw JSON, to the application layer.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_character(self, character_id: int) -> CharacterInfo:
        resp = await self._client.get(f"{ESI_BASE}/characters/{character_id}/")
        resp.raise_for_status()
        data = resp.json()
        return CharacterInfo(name=data["name"], corporation_id=data["corporation_id"])

    async def get_character_corporation(self, character_id: int) -> int:
        return (await self.get_character(character_id)).corporation_id

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        resp = await self._client.get(f"{ESI_BASE}/corporations/{corporation_id}/")
        resp.raise_for_status()
        data = resp.json()
        return CorporationInfo(
            name=data["name"], ceo_id=data["ceo_id"], ticker=data.get("ticker")
        )

    async def get_character_roles(
        self, character_id: int, access_token: str
    ) -> list[str]:
        """Authenticated corp-roles lookup. Returns [] if the scope wasn't granted."""
        resp = await self._client.get(
            f"{ESI_BASE}/characters/{character_id}/roles/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            return []  # scope not granted — fail closed (ADR-0015)
        resp.raise_for_status()
        return resp.json().get("roles", [])

    async def get_corporation_members(
        self, corporation_id: int, access_token: str
    ) -> list[int]:
        """The corp's member character ids (ADR-0036). Requires a token whose character
        has permission to read membership (the membership scope, plus any in-game role EVE
        requires); 401/403 means that character can't read the roster (raised, not
        swallowed, so the sync can explain)."""
        resp = await self._client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/members/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            raise CorporationMembersForbidden()
        resp.raise_for_status()
        return resp.json()

    async def resolve_universe_names(
        self, ids: list[int], *, categories: tuple[str, ...] = ("character",)
    ) -> dict[int, str]:
        """Resolve ids to names via the public bulk endpoint (ADR-0036), keeping only
        the requested categories (characters by default; payment reconciliation also
        asks for corporations). Chunked at ESI's 1000-id limit; empty input is a no-op."""
        names: dict[int, str] = {}
        for start in range(0, len(ids), _NAMES_CHUNK):
            chunk = ids[start : start + _NAMES_CHUNK]
            resp = await self._client.post(f"{ESI_BASE}/universe/names/", json=chunk)
            resp.raise_for_status()
            for entry in resp.json():
                if entry.get("category") in categories:
                    names[entry["id"]] = entry["name"]
        return names

    async def get_character_wallet_journal(
        self, character_id: int, access_token: str
    ) -> list[WalletJournalEntry]:
        """The operator character's wallet journal (ADR-0042), most recent first. Only
        the first page is read: ESI pages hold thousands of entries and the
        reconciliation job polls far more often than one page's worth of activity;
        entries are deduplicated by journal id downstream anyway. Money is parsed
        JSON-number → Decimal directly (ADR-0020). 401/403 → `CharacterWalletForbidden`."""
        resp = await self._client.get(
            f"{ESI_BASE}/characters/{character_id}/wallet/journal/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            raise CharacterWalletForbidden()
        resp.raise_for_status()
        return [
            WalletJournalEntry.model_validate(raw)
            for raw in json.loads(resp.text, parse_float=Decimal)
        ]

    async def get_corporation_contracts(
        self, corporation_id: int, access_token: str
    ) -> list[CorporationContract]:
        """The corp's **item-exchange** contracts (ADR-0037), paginated. Needs the
        contracts scope + an in-game role; 401/403 → `CorporationContractsForbidden`.
        Money is parsed JSON-number → Decimal directly to avoid a float round-trip
        (ADR-0020). Non-item-exchange contracts are dropped here."""
        url = f"{ESI_BASE}/corporations/{corporation_id}/contracts/"
        headers = {"Authorization": f"Bearer {access_token}"}
        contracts: list[CorporationContract] = []
        page = 1
        while True:
            resp = await self._client.get(
                url, params={"page": page, "datasource": "tranquility"}, headers=headers
            )
            if page == 1 and scope_missing(resp):
                raise CorporationContractsForbidden()
            resp.raise_for_status()
            for raw in json.loads(resp.text, parse_float=Decimal):
                if raw.get("type") == "item_exchange":
                    contracts.append(CorporationContract.model_validate(raw))
            if page >= int(resp.headers.get("X-Pages", "1")):
                break
            page += 1
        return contracts

    async def get_corporation_wallet_transactions(
        self, corporation_id: int, division: int, access_token: str
    ) -> list[CorpWalletTransaction]:
        """The configured division's market fills (ADR-0045), most recent first. Only
        the latest page is read — the ingestion polls far more often than one page's
        worth of fills, and `transaction_id` dedupes downstream. Money is parsed
        JSON-number → Decimal (ADR-0020). 401/403 → `CorporationWalletForbidden`."""
        resp = await self._client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/wallets/{division}"
            "/transactions/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            raise CorporationWalletForbidden()
        resp.raise_for_status()
        return [
            CorpWalletTransaction.model_validate(raw)
            for raw in json.loads(resp.text, parse_float=Decimal)
        ]

    async def get_corporation_wallet_journal(
        self, corporation_id: int, division: int, access_token: str
    ) -> list[CorpJournalEntry]:
        """The configured division's journal (ADR-0045) — the tax and broker-fee
        entries. First page only, same rationale as transactions. 401/403 →
        `CorporationWalletForbidden`."""
        resp = await self._client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/wallets/{division}/journal/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            raise CorporationWalletForbidden()
        resp.raise_for_status()
        return [
            CorpJournalEntry.model_validate(raw)
            for raw in json.loads(resp.text, parse_float=Decimal)
        ]

    async def get_corporation_orders(
        self, corporation_id: int, access_token: str
    ) -> list[CorpMarketOrder]:
        """The corp's LIVE market orders (ADR-0045), paginated. 401/403 →
        `CorporationOrdersForbidden`."""
        return await self._orders(
            f"{ESI_BASE}/corporations/{corporation_id}/orders/", access_token
        )

    async def _orders(
        self, url: str, access_token: str
    ) -> list[CorpMarketOrder]:
        headers = {"Authorization": f"Bearer {access_token}"}
        orders: list[CorpMarketOrder] = []
        page = 1
        while True:
            resp = await self._client.get(
                url, params={"page": page, "datasource": "tranquility"}, headers=headers
            )
            if page == 1 and scope_missing(resp):
                raise CorporationOrdersForbidden()
            resp.raise_for_status()
            orders.extend(
                CorpMarketOrder.model_validate(raw)
                for raw in json.loads(resp.text, parse_float=Decimal)
            )
            if page >= int(resp.headers.get("X-Pages", "1")):
                break
            page += 1
        return orders

    async def get_corporation_assets(
        self, corporation_id: int, access_token: str
    ) -> list[CorporationAsset]:
        """The corp's assets (ADR-0044), paginated. Needs the assets scope + the
        Director role in game; 401/403 → `CorporationAssetsForbidden`."""
        url = f"{ESI_BASE}/corporations/{corporation_id}/assets/"
        headers = {"Authorization": f"Bearer {access_token}"}
        assets: list[CorporationAsset] = []
        page = 1
        while True:
            resp = await self._client.get(
                url, params={"page": page, "datasource": "tranquility"}, headers=headers
            )
            if page == 1 and scope_missing(resp):
                raise CorporationAssetsForbidden()
            resp.raise_for_status()
            assets.extend(CorporationAsset.model_validate(a) for a in resp.json())
            if page >= int(resp.headers.get("X-Pages", "1")):
                break
            page += 1
        return assets

    async def get_corporation_divisions(
        self, corporation_id: int, access_token: str
    ) -> CorporationDivisions:
        """The corp's wallet/hangar division names (ADR-0048). Needs the divisions
        scope + the Director role in game; 401/403 degrades to empty maps — the
        labels are cosmetic, so the UI falls back rather than erroring."""
        resp = await self._client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/divisions/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            return CorporationDivisions()
        resp.raise_for_status()
        data = resp.json()
        return CorporationDivisions(
            wallet=_named_divisions(data.get("wallet", [])),
            hangar=_named_divisions(data.get("hangar", [])),
        )

    async def get_corporation_contract_items(
        self, corporation_id: int, contract_id: int, access_token: str
    ) -> list[ContractItem]:
        """The items in one corp contract (ADR-0037), paginated. Same scope as the
        contracts list; 401/403 → `CorporationContractsForbidden`."""
        url = (
            f"{ESI_BASE}/corporations/{corporation_id}/contracts/{contract_id}/items/"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        items: list[ContractItem] = []
        page = 1
        while True:
            resp = await self._client.get(
                url, params={"page": page, "datasource": "tranquility"}, headers=headers
            )
            if page == 1 and scope_missing(resp):
                raise CorporationContractsForbidden()
            resp.raise_for_status()
            items.extend(ContractItem.model_validate(i) for i in resp.json())
            if page >= int(resp.headers.get("X-Pages", "1")):
                break
            page += 1
        return items

    async def open_contract_window(
        self, contract_id: int, access_token: str
    ) -> None:
        """Open a contract in the token-holder's running EVE client (ADR-0038), via
        `POST /ui/openwindow/contract/`. Needs the `esi-ui.open_window.v1` scope; 401/403 →
        `OpenWindowForbidden`. A success is HTTP 204 (no body)."""
        resp = await self._client.post(
            f"{ESI_BASE}/ui/openwindow/contract/",
            params={"contract_id": contract_id, "datasource": "tranquility"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if scope_missing(resp):
            raise OpenWindowForbidden()
        resp.raise_for_status()


def _named_divisions(entries: list[dict]) -> dict[int, str]:
    """`division` → `name` for the entries that carry a name (ESI omits it for
    divisions left at their in-game default)."""
    return {e["division"]: e["name"] for e in entries if e.get("name")}


def get_esi_client(request: Request) -> EsiClient:
    return EsiClient(request.app.state.http)
