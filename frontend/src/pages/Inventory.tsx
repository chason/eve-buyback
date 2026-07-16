import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router-dom"

import {
  addHangar,
  getInventory,
  getReprocessPreview,
  listHangars,
  listReconciliationEvents,
  recordReprocess,
  removeHangar,
  runHangarCheck,
} from "../api/accounting"
import type {
  InventoryItemOut,
  InventoryOut,
  ReconciliationEventOut,
} from "../api/accounting"
import { listLocations } from "../api/locations"
import AccountingAccessPanel from "../components/AccountingAccessPanel"
import { StatusChip } from "../components/StatusChip"
import { formatIsk, formatIskCompact } from "../lib/format"

/** "What we've got" (ADR-0043, #152): the buyback's current holdings in plain
 * English — how much of each item, what we paid for it, and how long it's been
 * sitting. No accounting jargon; costs compact (4.2B) with exact ISK on hover. */
export default function Inventory() {
  const result = useQuery({ queryKey: ["inventory"], queryFn: getInventory })

  if (result.isLoading) return <p aria-busy="true">Loading…</p>
  if (result.isError || !result.data) {
    return <p className="error">Could not load the stock list.</p>
  }

  if (!result.data.access) {
    return (
      <>
        <hgroup>
          <h1>What we&apos;ve got</h1>
          <p>Track what the buyback owns, what you paid, and what sells.</p>
        </hgroup>
        <AccountingAccessPanel />
      </>
    )
  }

  return <StockView inv={result.data.inventory} />
}

function StockView({ inv }: { inv: InventoryOut }) {
  // The reprocess record dialog (#177), opened from a buy row or a hangar-check
  // suggestion; one at a time, keyed by the source lot.
  const [reprocessLotId, setReprocessLotId] = useState<string | null>(null)
  // Valuation cards only make sense once something is priced (#153).
  const anythingPriced = inv.items.length > inv.unpriced_types
  // A suggestion targets a TYPE; the oldest buy of it is the FIFO-correct source.
  const oldestLotOf = (typeId: number) =>
    inv.items.find((i) => i.type_id === typeId)?.lots[0]?.id ?? null
  return (
    <>
      <hgroup>
        <h1>What we&apos;ve got</h1>
        <p>Everything the buyback bought and still holds, at what we paid.</p>
      </hgroup>

      <div className="inventory-summary">
        <SummaryCard label="Everything we hold" value={inv.total_cost} />
        <SummaryCard label="Bought through the app" value={inv.verified_cost} />
        {inv.estimated_cost !== "0" && Number(inv.estimated_cost) !== 0 && (
          <SummaryCard label="Estimated value" value={inv.estimated_cost} />
        )}
        {anythingPriced && (
          <>
            <SummaryCard label="If we sold it all today" value={inv.worth_total} />
            <GainLossCard value={inv.unrealized_total} />
          </>
        )}
      </div>

      {inv.items.length === 0 ? (
        <p>
          Nothing in stock yet. When a buyback contract completes, what you bought
          shows up here automatically.
        </p>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th className="num">How many</th>
                <th className="num">What we paid</th>
                <th className="num">Worth now</th>
                <th>Sitting for</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {inv.items.map((item) => (
                <ItemRows
                  key={item.type_id}
                  item={item}
                  onReprocess={setReprocessLotId}
                />
              ))}
            </tbody>
          </table>
          {inv.unpriced_types > 0 && (
            <small className="field-hint">
              {inv.unpriced_types === 1
                ? "1 item has no current market price, so it isn't counted in the totals."
                : `${inv.unpriced_types} items have no current market price, so they aren't counted in the totals.`}
            </small>
          )}
        </div>
      )}

      {reprocessLotId && (
        <ReprocessPanel
          lotId={reprocessLotId}
          onClose={() => setReprocessLotId(null)}
        />
      )}

      <HangarsSection />
      <ReconciliationSection
        onRecordReprocess={(typeId) => {
          const lotId = oldestLotOf(typeId)
          if (lotId) setReprocessLotId(lotId)
        }}
      />
    </>
  )
}

/** The reprocess record form (ADR-0047, #177): "Turned into minerals — what we
 * paid carries over." Output quantities pre-fill from the game's base yields and
 * stay editable, because real yields vary with skills and structures. */
function ReprocessPanel({
  lotId,
  onClose,
}: {
  lotId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const preview = useQuery({
    queryKey: ["reprocessPreview", lotId],
    queryFn: () => getReprocessPreview(lotId),
  })
  const [qty, setQty] = useState<number | null>(null)
  const [quantities, setQuantities] = useState<Record<number, number> | null>(
    null,
  )
  const record = useMutation({
    mutationFn: () =>
      recordReprocess(
        lotId,
        qty ?? preview.data?.qty_remaining ?? 0,
        Object.entries(quantities ?? prefill())
          .map(([tid, quantity]) => ({ type_id: Number(tid), quantity }))
          .filter((o) => o.quantity > 0),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      onClose()
    },
  })

  const prefill = (): Record<number, number> =>
    Object.fromEntries(
      (preview.data?.outputs ?? []).map((o) => [o.type_id, o.quantity]),
    )

  if (preview.isLoading) return <p aria-busy="true">Loading…</p>
  if (preview.isError || !preview.data) {
    return <p className="error">Could not load the reprocess form.</p>
  }
  const p = preview.data
  const outputs = quantities ?? prefill()

  return (
    <section className="panel">
      <h2>Turned into minerals</h2>
      <p>
        <small className="field-hint">
          Record what {p.type_name ?? `Type ${p.type_id}`} was reprocessed into —
          what we paid for it carries over into the minerals. Adjust the amounts
          to what you actually got.
        </small>
      </p>
      <div className="access-actions">
        <label>
          Units reprocessed
          <input
            type="number"
            min={1}
            max={p.qty_remaining}
            value={qty ?? p.qty_remaining}
            onChange={(e) => setQty(Number(e.target.value))}
          />
        </label>
      </div>
      {p.outputs.length === 0 && (
        <p>
          <small className="field-hint">
            No game data for this item&apos;s yields — this form can&apos;t
            pre-fill it yet.
          </small>
        </p>
      )}
      <div className="access-actions">
        {p.outputs.map((o) => (
          <label key={o.type_id}>
            {o.type_name ?? `Type ${o.type_id}`}
            <input
              type="number"
              min={0}
              value={outputs[o.type_id] ?? 0}
              onChange={(e) =>
                setQuantities({
                  ...outputs,
                  [o.type_id]: Number(e.target.value),
                })
              }
            />
          </label>
        ))}
      </div>
      <p>
        <button
          type="button"
          className="secondary"
          disabled={
            record.isPending ||
            Object.values(outputs).every((q) => !q || q <= 0)
          }
          onClick={() => record.mutate()}
        >
          {record.isPending ? "Recording…" : "Record it"}
        </button>{" "}
        <button type="button" className="linkbtn" onClick={onClose}>
          Cancel
        </button>
        {record.isError && (
          <small className="error">{(record.error as Error).message}</small>
        )}
      </p>
    </section>
  )
}

/** The hangar-check log (ADR-0044, #155): what each sync changed, flagged entries
 * first-class — plus a button to run a check right now. Plain English only. A
 * reprocess suggestion (#177) carries a "Record it" button into the form. */
function ReconciliationSection({
  onRecordReprocess,
}: {
  onRecordReprocess: (typeId: number) => void
}) {
  const queryClient = useQueryClient()
  const events = useQuery({
    queryKey: ["reconciliation"],
    queryFn: listReconciliationEvents,
  })
  const check = useMutation({
    mutationFn: runHangarCheck,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })

  return (
    <section className="panel">
      <h2>Hangar checks</h2>
      <p>
        <small className="field-hint">
          The app regularly compares the books against what&apos;s actually in your
          marked hangars: stock it finds beyond the books is added at estimated
          value, and anything missing is flagged here.
        </small>
      </p>
      <p>
        <button
          type="button"
          className="secondary"
          disabled={check.isPending}
          onClick={() => check.mutate()}
        >
          {check.isPending ? "Checking…" : "Check the hangar now"}
        </button>{" "}
        {check.isSuccess && (
          <small className="field-hint" role="status">
            {checkSummary(check.data)}
          </small>
        )}
        {check.isError && (
          <small className="error">{(check.error as Error).message}</small>
        )}
      </p>
      {events.data && events.data.length > 0 && (
        <ul className="recon-list">
          {events.data.map((e, i) => (
            <li key={i} className={e.flagged ? "recon-flagged" : undefined}>
              <small>
                {new Date(e.occurred_at).toLocaleString()} — {eventText(e)}
                {e.kind === "reprocess_hint" && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="linkbtn"
                      onClick={() => onRecordReprocess(e.type_id)}
                    >
                      Record it
                    </button>
                  </>
                )}
              </small>
            </li>
          ))}
        </ul>
      )}
      {events.data && events.data.length === 0 && (
        <p>
          <small className="field-hint">Nothing to report yet.</small>
        </p>
      )}
    </section>
  )
}

function checkSummary(r: { lots_added: number; flagged: number }): string {
  if (r.lots_added === 0 && r.flagged === 0) {
    return "All good — the hangar matches the books."
  }
  const parts = []
  if (r.lots_added > 0) {
    parts.push(`${r.lots_added} ${r.lots_added === 1 ? "item" : "items"} added`)
  }
  if (r.flagged > 0) parts.push(`${r.flagged} flagged for a look`)
  return `Done — ${parts.join(", ")}.`
}

function eventText(e: ReconciliationEventOut): string {
  const item = e.type_name ?? `Type ${e.type_id}`
  const where = e.location_name ?? e.location_id
  const qty = e.qty.toLocaleString()
  if (e.kind === "reprocess_hint") {
    return `Looks like ${qty} ${item} at ${where} was turned into minerals — record it to carry what we paid into them.`
  }
  if (e.kind === "shortfall") {
    return `${qty} ${item} missing at ${where} — sold or moved outside the app?`
  }
  if (!e.booked) {
    return `Found ${qty} ${item} at ${where} — no market price to value it yet.`
  }
  return `Found ${qty} ${item} at ${where} — added at estimated value${
    e.unit_cost ? ` (${formatIsk(e.unit_cost)} each)` : ""
  }.`
}

/** Buyback-hangar config (ADR-0044, #154): which corp hangar divisions hold buyback
 * stock. The location picker offers the corp's drop-off locations — that's where
 * members deliver. The check itself (books vs hangar) rides a background sync. */
function HangarsSection() {
  const queryClient = useQueryClient()
  const hangars = useQuery({ queryKey: ["hangars"], queryFn: listHangars })
  const locations = useQuery({ queryKey: ["locations"], queryFn: listLocations })
  const [locationId, setLocationId] = useState("")
  const [division, setDivision] = useState(1)
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["hangars"] })
  const add = useMutation({
    mutationFn: () => addHangar(locationId, division),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (h: { location_id: string; division: number }) =>
      removeHangar(h.location_id, h.division),
    onSuccess: invalidate,
  })

  return (
    <section className="panel">
      <h2>Our hangars</h2>
      <p>
        <small className="field-hint">
          Mark the corp hangars where buyback stock lives, so the app can check the
          books against what&apos;s actually in them.
        </small>
      </p>
      {hangars.data && hangars.data.length > 0 && (
        <ul className="hangar-list">
          {hangars.data.map((h) => (
            <li key={`${h.location_id}-${h.division}`}>
              {h.location_name} — hangar {h.division}{" "}
              <button
                type="button"
                className="linkbtn"
                onClick={() => remove.mutate(h)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {locations.data && locations.data.length === 0 ? (
        <p>
          <small className="field-hint">
            Add a drop-off location on the <Link to="/locations">Locations</Link>{" "}
            page first — hangars are picked from there.
          </small>
        </p>
      ) : (
        <div className="access-actions">
          <select
            aria-label="Hangar location"
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
          >
            <option value="">Pick a location…</option>
            {(locations.data ?? []).map((l) => (
              <option key={l.location_id} value={l.location_id}>
                {l.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Hangar division"
            value={division}
            onChange={(e) => setDivision(Number(e.target.value))}
          >
            {[1, 2, 3, 4, 5, 6, 7].map((d) => (
              <option key={d} value={d}>
                Hangar {d}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="secondary"
            disabled={!locationId || add.isPending}
            onClick={() => add.mutate()}
          >
            Add hangar
          </button>
          {add.isError && (
            <small className="error">{(add.error as Error).message}</small>
          )}
        </div>
      )}
    </section>
  )
}

/** The paper gain/loss card (#153): what selling everything today would mean
 * compared to what we paid — its own line, never folded into the holdings. */
function GainLossCard({ value }: { value: string }) {
  const negative = value.startsWith("-")
  return (
    <div className="inventory-card">
      <small>Compared to what we paid</small>
      <strong
        className={negative ? "isk worth-loss" : "isk"}
        title={formatIsk(value)}
      >
        {negative ? "" : "+"}
        {formatIskCompact(value)} ISK
      </strong>
    </div>
  )
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="inventory-card">
      <small>{label}</small>
      <strong className="isk" title={formatIsk(value)}>
        {formatIskCompact(value)} ISK
      </strong>
    </div>
  )
}

function ItemRows({
  item,
  onReprocess,
}: {
  item: InventoryItemOut
  onReprocess: (lotId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const multiple = item.lots.length > 1
  return (
    <>
      <tr>
        <td>{item.type_name ?? `Type ${item.type_id}`}</td>
        <td className="num">{item.qty.toLocaleString()}</td>
        <td className="num isk">
          {item.any_estimated && (
            <>
              <StatusChip variant="info">Estimated value</StatusChip>{" "}
            </>
          )}
          <span title={formatIsk(item.total_cost)}>
            {formatIskCompact(item.total_cost)}
          </span>
        </td>
        <td className="num isk">
          <WorthNow worth={item.worth} unrealized={item.unrealized} />
        </td>
        <td>
          <DaysHeld days={item.oldest_days} stale={item.stale} />
        </td>
        <td>
          {multiple ? (
            <button
              type="button"
              className="linkbtn"
              aria-expanded={open}
              onClick={() => setOpen(!open)}
            >
              {open ? "Hide" : `${item.lots.length} buys`}
            </button>
          ) : (
            item.lots.length === 1 && (
              <button
                type="button"
                className="linkbtn"
                onClick={() => onReprocess(item.lots[0].id)}
              >
                Turned into minerals
              </button>
            )
          )}
        </td>
      </tr>
      {open &&
        item.lots.map((lot, i) => (
          <tr key={i} className="inventory-lot-row">
            <td>
              <small>
                {new Date(lot.acquired_at).toLocaleDateString(undefined, {
                  timeZone: "UTC",
                })}
              </small>
            </td>
            <td className="num">
              <small>{lot.qty.toLocaleString()}</small>
            </td>
            <td className="num isk">
              <small>
                {lot.cost_is_estimated && (
                  <>
                    <StatusChip variant="info">Estimated value</StatusChip>{" "}
                  </>
                )}
                <span title={formatIsk(lot.total_cost)}>
                  {formatIsk(lot.unit_cost)} each
                </span>
              </small>
            </td>
            <td />
            <td>
              <small>
                <DaysHeld days={lot.days_held} stale={lot.stale} />
              </small>
            </td>
            <td>
              <button
                type="button"
                className="linkbtn"
                onClick={() => onReprocess(lot.id)}
              >
                <small>Turned into minerals</small>
              </button>
            </td>
          </tr>
        ))}
    </>
  )
}

/** What the holding would fetch today (#153). A dash when the market cache has no
 * price for it; danger-red with a plain-English tooltip when it's worth less than
 * we paid. Exact ISK on hover either way. */
function WorthNow({
  worth,
  unrealized,
}: {
  worth?: string | null
  unrealized?: string | null
}) {
  if (worth == null) return <>—</>
  const losing = unrealized != null && unrealized.startsWith("-")
  const compact = <span title={formatIsk(worth)}>{formatIskCompact(worth)}</span>
  if (!losing) return compact
  return (
    <span className="worth-loss" title="Worth less than we paid">
      {compact}
    </span>
  )
}

/** Aging readout: past the stale threshold the number itself turns danger-red and
 * the plain-English explanation rides a tooltip instead of a chip. */
function DaysHeld({ days, stale }: { days: number; stale: boolean }) {
  if (!stale) return <>{daysText(days)}</>
  return (
    <span className="stale-days" title="Sitting a while">
      {daysText(days)}
    </span>
  )
}

function daysText(days: number): string {
  if (days === 0) return "today"
  if (days === 1) return "1 day"
  return `${days.toLocaleString()} days`
}
