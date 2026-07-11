# 0047. Lot transformations: reprocessing flows cost into child lots

- **Status:** Proposed
- **Date:** 2026-07-11
- **Relates to:** [ADR-0043](0043-lot-based-buyback-accounting.md) (the lot ledger this
  amends — transformation joins acquisition and sale as a lot event),
  [ADR-0044](0044-hangar-inventory-reconciliation.md) (reconciliation must recognize a
  reprocess, not misread it as loss + windfall),
  [ADR-0026](0026-ore-reprocess-pricing.md) (the yield + valuation machinery reused for
  cost allocation), [ADR-0020](0020-decimal-money-values.md) (Decimal money),
  [ADR-0042](0042-paid-accounting-entitlements.md) (gated with the rest of the add-on)

## Context

Buybacks routinely take in **ore** that the corp then reprocesses into **minerals**
before selling. Physically the ore vanishes and minerals appear; financially it is the
same capital — the corp paid for the ore, and that cost must carry into the minerals.
This is the classic **inventory transformation** problem (raw material → outputs, cost
flows through), with EVE's twist: one input yields **several** outputs (a joint-products
allocation), and **ESI has no reprocessing signal** — reprocessing is an instant client
action, not an industry job, so no API event says it happened.

Without an explicit model, the hangar reconciliation (ADR-0044) would see the ore vanish
(a flagged shortfall) and minerals appear (deemed-cost excess lots) — severing the cost
lineage and replacing a *known* cost basis with an estimate. Exactly what a ledger that
promises "inventory at cost" must not do.

## Decision

**A `reprocess` transformation event consumes quantity from an ore lot and creates child
mineral lots whose combined cost basis equals exactly the ore cost consumed, allocated
across the minerals pro-rata by market value at split-off.**

- **Child lots, not same-row tracking.** Minerals are distinct type_ids with their own
  FIFO queues, so each output mineral becomes its **own lot** carrying
  `source_lot_id` → the ore lot. Provenance chains end to end: mineral lot → ore lot →
  appraisal → contract.
- **Cost conservation, no write-up.** `Σ(child lot costs) = ore cost consumed` (per
  ADR-0020, Decimal). If 1M ISK of ore becomes minerals "worth" 1.3M, the ledger still
  carries 1M until they actually sell — the 0.3M stays **unrealized** (ADR-0043's
  conservatism survives transformation).
- **Joint-cost allocation by relative market value at split-off** — the standard
  answer to one-input/many-outputs, and the app already computes those numbers: the
  ADR-0026 reprocess machinery (`sde_type_material` yields + the cached market prices)
  provides each mineral's market value at the moment of reprocess. Physical-quantity
  allocation is rejected: it misprices scarce minerals (a unit of Megacyte and a unit
  of Tritanium are not the same capital).
- **Inheritance.** `cost_is_estimated` propagates from the ore lot (deemed-cost ore →
  deemed-cost minerals; measured and estimated never blend, ADR-0043). `acquired_at`
  is inherited too — aging answers "how long has this capital been tied up," not "how
  recently was the reprocess button pressed."
- **Reprocessing tax is absorbed.** The station/structure takes a share of the output;
  the full consumed ore cost flows onto the minerals actually **received**. Conservative
  and simple — an explicit tax expense line can be added later if wanted.
- **Recording the event — two stages:**
  1. **Manual action (v1):** a manager records a reprocess on an ore lot with the
     mineral quantities received. Plain-English UI: *"Turned into minerals — what we
     paid for the ore carries over."*
  2. **Hangar-assisted (with ADR-0044):** when reconciliation sees an ore shortfall
     and a mineral excess **consistent with that ore's yields**, it offers a one-click
     *"record a reprocess?"* suggestion in the "Needs a look" list instead of flagging
     a loss and inventing deemed-cost mineral lots.
- **Schema hook lands with the base ledger.** The lots table carries `source_lot_id`
  (nullable self-FK) and the ledger includes a `lot_transformations` event table from
  the start (#150) — the columns are near-free now and avoid a migration + backfill
  when the behavior ships.

## Consequences

- Reprocessed inventory keeps a **verified cost basis** — per-mineral margins and the
  per-type feedback reports (ADR-0043's margin/turnover table) stay honest for the
  ore→mineral pipeline, which for many buybacks is most of their volume.
- A new lot **source** joins `buyback`/`opening_balance`/`manual`: children of a
  transformation (surfaced in plain English as "from reprocessing").
- The hangar reconciliation gains pattern-recognition scope (yield matching) — a
  suggestion, never an auto-action: quantities won't match yields exactly (skills,
  structure bonuses, partial batches), so a human confirms.
- Transformation is generic on purpose: the same event shape covers future cases
  (e.g. repackaging, manufacturing) without new machinery.

## Alternatives considered

- **Track minerals inside the ore lot row** — different type_ids, separate market
  prices, separate FIFO consumption; a single row cannot express that. Rejected in
  favor of child lots + provenance link.
- **Let hangar reconciliation re-enter minerals at deemed cost** — severs the cost
  lineage, silently converts measured cost into an estimate, and misstates P&L twice
  (a fictional ore loss, then fictional mineral basis). Rejected — this ADR exists to
  prevent precisely that.
- **Allocate ore cost by output quantity/volume** — misprices jointly-produced
  minerals; market-value allocation is the accounting standard for joint products.
  Rejected.
- **Set the minerals' basis to their market value at reprocess** — books unrealized
  gain as cost (a write-up), violating ADR-0043's conservatism. Rejected.
- **Detect reprocessing via ESI industry jobs** — reprocessing is not an industry job
  and never appears there; no ESI surface exists. Manual + hangar-assisted is the
  honest design.
