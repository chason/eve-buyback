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

Buybacks routinely take in **ore** that the corp reprocesses into **minerals** before
selling — but ore is only the most common case. **Almost any item can be reprocessed**,
and buybacks do it deliberately: when an item's market price sinks below its mineral
content, the minerals *are* the profitable exit ("reprocess arbitrage" on cheap modules,
ships, and salvage). Physically the source items vanish and materials appear; financially
it is the same capital — the corp paid for the items, and that cost must carry into the
outputs. This is the classic **inventory transformation** problem (input → outputs, cost
flows through), with EVE's twists: one input yields **several** outputs (a joint-products
allocation), and **ESI has no reprocessing signal** — reprocessing is an instant client
action, not an industry job, so no API event says it happened.

Without an explicit model, the hangar reconciliation (ADR-0044) would see the source
items vanish (a flagged shortfall) and materials appear (deemed-cost excess lots) —
severing the cost lineage and replacing a *known* cost basis with an estimate. Exactly
what a ledger that promises "inventory at cost" must not do.

## Decision

**A `reprocess` transformation event consumes quantity from ANY lot and creates child
material lots whose combined cost basis equals exactly the source cost consumed,
allocated across the outputs pro-rata by market value at split-off.**

- **Source-agnostic by design.** The event takes any lot — ore, modules, ships, salvage;
  no type restriction. (The yield data already covers this: the SDE seed ingests
  `invTypeMaterials` **unfiltered**, so base yields exist for every reprocessable type —
  ADR-0026 merely *uses* them for ore pricing.)
- **Child lots, not same-row tracking.** Output materials are distinct type_ids with
  their own FIFO queues, so each becomes its **own lot** carrying `source_lot_id` → the
  source lot. Provenance chains end to end: material lot → source lot → appraisal →
  contract.
- **Cost conservation, no write-up.** `Σ(child lot costs) = source cost consumed` (per
  ADR-0020, Decimal). If 1M ISK of input becomes materials "worth" 1.3M, the ledger
  still carries 1M until they actually sell — the 0.3M stays **unrealized** (ADR-0043's
  conservatism survives transformation).
- **Joint-cost allocation by relative market value at split-off** — the standard
  answer to one-input/many-outputs, and the app already computes those numbers: the
  ADR-0026 machinery (`sde_type_material` yields + the cached market prices) provides
  each output's market value at the moment of reprocess. Physical-quantity allocation
  is rejected: it misprices scarce materials (a unit of Megacyte and a unit of
  Tritanium are not the same capital).
- **Inheritance.** `cost_is_estimated` propagates from the source lot (deemed-cost
  input → deemed-cost outputs; measured and estimated never blend, ADR-0043).
  `acquired_at` is inherited too — aging answers "how long has this capital been tied
  up," not "how recently was the reprocess button pressed."
- **Reprocessing tax is absorbed.** The station/structure takes a share of the output;
  the full consumed source cost flows onto the materials actually **received**.
  Conservative and simple — an explicit tax expense line can be added later if wanted.
- **Recording the event — two stages:**
  1. **Manual action (v1):** a manager records a reprocess on any lot with the output
     quantities received (pre-filled from the type's base yields where known). Plain-
     English UI: *"Turned into minerals — what we paid carries over."*
  2. **Hangar-assisted (with ADR-0044):** when reconciliation sees a shortfall of a
     reprocessable type and a materials excess **consistent with that type's yields**,
     it offers a one-click *"record a reprocess?"* suggestion in the "Needs a look"
     list instead of flagging a loss and inventing deemed-cost material lots.
- **Schema hook lands with the base ledger.** The lots table carries `source_lot_id`
  (nullable self-FK) and the ledger includes a `lot_transformations` event table from
  the start (#150) — the columns are near-free now and avoid a migration + backfill
  when the behavior ships.

## Consequences

- Reprocessed inventory keeps a **verified cost basis** — per-material margins and the
  per-type feedback reports (ADR-0043's margin/turnover table) stay honest for the
  reprocessing pipeline (ore→minerals, and the low-price-item→minerals arbitrage),
  which for many buybacks is most of their volume.
- A new lot **source** joins `buyback`/`opening_balance`/`manual`: children of a
  transformation (surfaced in plain English as "from reprocessing").
- The hangar reconciliation gains pattern-recognition scope (yield matching) — a
  suggestion, never an auto-action: quantities won't match yields exactly (skills,
  structure bonuses, partial batches), so a human confirms.
- Transformation is generic on purpose: the same event shape covers future cases
  (e.g. repackaging, manufacturing) without new machinery.

## Alternatives considered

- **Restrict transformations to ore** — most items reprocess, and the low-price-item
  arbitrage is a deliberate buyback strategy; an ore-only event would push everything
  else through the lineage-severing reconciliation path. The yield data is already
  seeded unfiltered, so generality costs nothing. Rejected.
- **Track outputs inside the source lot row** — different type_ids, separate market
  prices, separate FIFO consumption; a single row cannot express that. Rejected in
  favor of child lots + provenance link.
- **Let hangar reconciliation re-enter materials at deemed cost** — severs the cost
  lineage, silently converts measured cost into an estimate, and misstates P&L twice
  (a fictional loss, then a fictional basis). Rejected — this ADR exists to prevent
  precisely that.
- **Allocate source cost by output quantity/volume** — misprices jointly-produced
  materials; market-value allocation is the accounting standard for joint products.
  Rejected.
- **Set the outputs' basis to their market value at reprocess** — books unrealized
  gain as cost (a write-up), violating ADR-0043's conservatism. Rejected.
- **Detect reprocessing via ESI industry jobs** — reprocessing is not an industry job
  and never appears there; no ESI surface exists. Manual + hangar-assisted is the
  honest design.
