import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router-dom"

import {
  addHangar,
  clearReceivable,
  confirmMoveSuggestion,
  createReceivable,
  dismissMoveSuggestion,
  getDivisionNames,
  getInventory,
  getReprocessPreview,
  getWalletDivision,
  listHangars,
  listMoveSuggestions,
  listReceivables,
  listReconciliationEvents,
  listShipments,
  markShipmentArrived,
  recordManualExpense,
  recordManualLot,
  recordManualSale,
  recordReprocess,
  recordShipment,
  removeHangar,
  runHangarCheck,
  setWalletDivision,
} from "../api/accounting"
import type {
  InventoryItemOut,
  InventoryOut,
  MoveSuggestionOut,
  ReconciliationEventOut,
  ShipmentOut,
} from "../api/accounting"
import { listLocations } from "../api/locations"
import { searchTypes } from "../api/sde"
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

/** What the "Record by hand" form opens pre-filled with (#158) — e.g. resolving a
 * hangar-check shortfall as the off-app sale it probably was. */
type ManualPrefill = {
  type_id: number
  type_name: string | null
  qty: number
  location_id: string
}

function StockView({ inv }: { inv: InventoryOut }) {
  // The reprocess record dialog (#177), opened from a buy row or a hangar-check
  // suggestion; one at a time, keyed by the source lot.
  const [reprocessLotId, setReprocessLotId] = useState<string | null>(null)
  // The manual-entry form's prefill (#158); bumping the key remounts the form.
  const [manualPrefill, setManualPrefill] = useState<ManualPrefill | null>(null)
  const [manualKey, setManualKey] = useState(0)
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

      <ManualEntrySection key={manualKey} prefill={manualPrefill} />
      <ReceivablesSection />
      <HangarsSection />
      <HaulsSection />
      <WalletSection />
      <ReconciliationSection
        onRecordReprocess={(typeId) => {
          const lotId = oldestLotOf(typeId)
          if (lotId) setReprocessLotId(lotId)
        }}
        onRecordSale={(e) => {
          setManualPrefill({
            type_id: e.type_id,
            type_name: e.type_name ?? null,
            qty: e.qty,
            location_id: e.location_id,
          })
          setManualKey((k) => k + 1)
        }}
      />
    </>
  )
}

/** The corp's real division names (ADR-0048), as `division → name` lookups. Both
 * pickers share the one query; when the corp token can't provide names (no grant,
 * missing scope, not a Director) the maps stay empty and generic labels apply. */
function useDivisionNames() {
  const divisions = useQuery({
    queryKey: ["divisionNames"],
    queryFn: getDivisionNames,
  })
  return {
    wallet: new Map(divisions.data?.wallet.map((d) => [d.division, d.name])),
    hangar: new Map(divisions.data?.hangar.map((d) => [d.division, d.name])),
  }
}

const EXPENSE_KINDS = [
  ["hauling", "Hauling"],
  ["broker_fee", "Broker fee"],
  ["relist_fee", "Relist fee"],
  ["other", "Something else"],
] as const

/** The manual-entry escape hatch (ADR-0045, #158): off-game sales, stock bought
 * outside the app, expenses, and ISK owed to the buyback — everything ESI can't
 * see, entered by hand into the same books with a required note. */
function ManualEntrySection({ prefill }: { prefill: ManualPrefill | null }) {
  const queryClient = useQueryClient()
  const locations = useQuery({ queryKey: ["locations"], queryFn: listLocations })
  const [kind, setKind] = useState<"sale" | "lot" | "expense" | "receivable">(
    "sale",
  )
  const [typeQuery, setTypeQuery] = useState("")
  const [picked, setPicked] = useState<{ id: number; name: string } | null>(
    prefill ? { id: prefill.type_id, name: prefill.type_name ?? "" } : null,
  )
  const [qty, setQty] = useState(prefill?.qty ?? 0)
  const [isk, setIsk] = useState("")  // unit proceeds / unit cost / amount
  const [locationId, setLocationId] = useState(prefill?.location_id ?? "")
  const [estimated, setEstimated] = useState(false)
  const [expenseKind, setExpenseKind] = useState<string>("hauling")
  const [note, setNote] = useState("")
  const [feedback, setFeedback] = useState<string | null>(null)
  const search = useQuery({
    queryKey: ["typeSearch", typeQuery],
    queryFn: () => searchTypes(typeQuery),
    enabled: typeQuery.length >= 2,
  })

  const submit = useMutation({
    mutationFn: async () => {
      if (kind === "sale") {
        return recordManualSale({
          type_id: picked!.id, qty, unit_proceeds: isk,
          location_id: locationId, note,
        })
      }
      if (kind === "lot") {
        await recordManualLot({
          type_id: picked!.id, qty, unit_cost: isk, location_id: locationId,
          note, cost_is_estimated: estimated,
        })
        return null
      }
      if (kind === "expense") {
        await recordManualExpense({ kind: expenseKind, amount: isk, note })
        return null
      }
      await createReceivable(isk, note)
      return null
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      void queryClient.invalidateQueries({ queryKey: ["receivables"] })
      setNote("")
      setIsk("")
      submit.reset()
      if (result && result.stock_was_missing) {
        setFeedback(
          "Recorded — heads up: the books didn't have that stock, so it was " +
            "added at estimated value and flagged in Hangar checks.",
        )
      } else {
        setFeedback("Recorded.")
      }
    },
  })

  const needsItem = kind === "sale" || kind === "lot"
  const ready =
    note.trim().length >= 3 &&
    isk !== "" &&
    (!needsItem || (picked && qty > 0 && locationId))

  return (
    <section className="panel">
      <h2>Record by hand</h2>
      <p>
        <small className="field-hint">
          For anything the app can&apos;t see on its own: deals made outside the
          game&apos;s market, stock bought off-app, costs, or ISK someone still
          owes the buyback. A short note is required — future-you will thank you.
        </small>
      </p>
      <div className="access-actions">
        <select
          aria-label="What to record"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as typeof kind)
            setFeedback(null)
          }}
        >
          <option value="sale">A sale outside the market</option>
          <option value="lot">Stock we bought off-app</option>
          <option value="expense">An expense</option>
          <option value="receivable">ISK owed to us</option>
        </select>
      </div>
      {needsItem && (
        <div className="access-actions">
          {picked ? (
            <span>
              {picked.name || `Type ${picked.id}`}{" "}
              <button
                type="button"
                className="linkbtn"
                onClick={() => setPicked(null)}
              >
                Change item
              </button>
            </span>
          ) : (
            <label>
              Item
              <input
                type="search"
                placeholder="Search items…"
                value={typeQuery}
                onChange={(e) => setTypeQuery(e.target.value)}
              />
              {(search.data ?? []).length > 0 && (
                <ul className="search-results">
                  {(search.data ?? []).slice(0, 8).map((t) => (
                    <li key={t.type_id}>
                      <button
                        type="button"
                        className="linkbtn"
                        onClick={() => {
                          setPicked({ id: t.type_id, name: t.name })
                          setTypeQuery("")
                        }}
                      >
                        {t.name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </label>
          )}
          <label>
            How many
            <input
              type="number"
              min={1}
              value={qty || ""}
              onChange={(e) => setQty(Number(e.target.value))}
            />
          </label>
          <select
            aria-label="Where"
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
          >
            <option value="">Where…</option>
            {(locations.data ?? []).map((l) => (
              <option key={l.location_id} value={l.location_id}>
                {l.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="access-actions">
        {kind === "expense" && (
          <select
            aria-label="Expense kind"
            value={expenseKind}
            onChange={(e) => setExpenseKind(e.target.value)}
          >
            {EXPENSE_KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
        <label>
          {kind === "sale"
            ? "ISK per unit"
            : kind === "lot"
              ? "What we paid per unit"
              : "ISK"}
          <input
            type="number"
            min={0}
            step="0.01"
            value={isk}
            onChange={(e) => setIsk(e.target.value)}
          />
        </label>
        {kind === "lot" && (
          <label>
            <input
              type="checkbox"
              checked={estimated}
              onChange={(e) => setEstimated(e.target.checked)}
            />
            The price is a guess
          </label>
        )}
      </div>
      <div className="access-actions">
        <label className="manual-note">
          Note
          <input
            type="text"
            placeholder="What happened? (required)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="secondary"
          disabled={!ready || submit.isPending}
          onClick={() => submit.mutate()}
        >
          {submit.isPending ? "Adding…" : "Add to the books"}
        </button>
      </div>
      {feedback && (
        <p>
          <small className="field-hint" role="status">
            {feedback}
          </small>
        </p>
      )}
      {submit.isError && (
        <p>
          <small className="error">{(submit.error as Error).message}</small>
        </p>
      )}
    </section>
  )
}

/** ISK owed to the buyback (ADR-0045, #158): wrong-wallet payments held as an
 * honest asset until the ISK actually moves. Rendered only when any exist. */
function ReceivablesSection() {
  const queryClient = useQueryClient()
  const receivables = useQuery({
    queryKey: ["receivables"],
    queryFn: listReceivables,
  })
  const clear = useMutation({
    mutationFn: (id: string) => clearReceivable(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["receivables"] }),
  })
  const open = (receivables.data ?? []).filter((r) => r.cleared_at === null)
  if (open.length === 0) return null

  return (
    <section className="panel">
      <h2>ISK owed to us</h2>
      <ul className="recon-list">
        {open.map((r) => (
          <li key={r.id}>
            <small>
              <strong className="isk">{formatIsk(r.amount)}</strong> — {r.note}{" "}
              <button
                type="button"
                className="linkbtn"
                onClick={() => clear.mutate(r.id)}
              >
                Mark paid
              </button>
            </small>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The buyback wallet division (ADR-0045, #156): the one corp wallet sales pay
 * into. Picking it switches on automatic sale/fee recording; "Not set" keeps the
 * sell side off. */
function WalletSection() {
  const queryClient = useQueryClient()
  const names = useDivisionNames()
  const division = useQuery({
    queryKey: ["walletDivision"],
    queryFn: getWalletDivision,
  })
  const save = useMutation({
    mutationFn: (d: number | null) => setWalletDivision(d),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["walletDivision"] }),
  })

  return (
    <section className="panel">
      <h2>Our wallet</h2>
      <p>
        <small className="field-hint">
          Which corp wallet do buyback sales pay into? Once picked, the app records
          market sales, taxes, and broker fees from that wallet automatically —
          sell orders paying into a different wallet get flagged below.
        </small>
      </p>
      <div className="access-actions">
        <select
          aria-label="Buyback wallet division"
          value={division.data?.division ?? ""}
          onChange={(e) =>
            save.mutate(e.target.value === "" ? null : Number(e.target.value))
          }
        >
          <option value="">Not set — sales aren&apos;t recorded</option>
          {[1, 2, 3, 4, 5, 6, 7].map((d) => (
            <option key={d} value={d}>
              {names.wallet.get(d) ?? `Wallet division ${d}`}
            </option>
          ))}
        </select>
        {save.isError && (
          <small className="error">{(save.error as Error).message}</small>
        )}
      </div>
    </section>
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
  onRecordSale,
}: {
  onRecordReprocess: (typeId: number) => void
  onRecordSale: (event: ReconciliationEventOut) => void
}) {
  const queryClient = useQueryClient()
  const events = useQuery({
    queryKey: ["reconciliation"],
    queryFn: listReconciliationEvents,
  })
  const suggestions = useQuery({
    queryKey: ["moveSuggestions"],
    queryFn: listMoveSuggestions,
  })
  const check = useMutation({
    mutationFn: runHangarCheck,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      void queryClient.invalidateQueries({ queryKey: ["moveSuggestions"] })
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
  // Confirming a move (#201) is a two-step: "Yes, this was a move" opens a
  // small prompt asking what the haul cost (#205, optional — leaving it empty
  // books nothing), and the confirm itself fires from there. On success the
  // card leaves the pending list, the stock list and the check log both
  // change — refetch all three.
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [haulCost, setHaulCost] = useState("")
  const confirmMove = useMutation({
    mutationFn: ({ id, cost }: { id: string; cost?: string }) =>
      confirmMoveSuggestion(id, cost),
    onSuccess: () => {
      setConfirmingId(null)
      setHaulCost("")
      void queryClient.invalidateQueries({ queryKey: ["moveSuggestions"] })
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
  // "Not a move" (#202): the card leaves the list; what's booked stays booked,
  // and the decision lands in the log below. A candidate card (#203) is
  // several suggestions — "not a move" answers every origin's question, so
  // each candidate is dismissed.
  const dismiss = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const id of ids) await dismissMoveSuggestion(id)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["moveSuggestions"] })
      void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
    },
  })

  // The #205 haul-cost prompt, shared by the plain confirm and the #203
  // candidate pick — by the time it opens, WHICH suggestion converts is fixed.
  const costPrompt = (id: string) => (
    <div className="access-actions">
      <label>
        What did the haul cost? (optional)
        <input
          type="number"
          min={0}
          step="0.01"
          placeholder="ISK — leave empty if nothing"
          value={haulCost}
          onChange={(e) => setHaulCost(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="secondary"
        disabled={confirmMove.isPending}
        onClick={() => confirmMove.mutate({ id, cost: haulCost || undefined })}
      >
        {confirmMove.isPending ? "Confirming…" : "Confirm the move"}
      </button>{" "}
      <button
        type="button"
        className="linkbtn"
        disabled={confirmMove.isPending}
        onClick={() => setConfirmingId(null)}
      >
        Cancel
      </button>
    </div>
  )

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
      {suggestions.data && suggestions.data.length > 0 && (
        <ul className="recon-list">
          {groupSuggestions(suggestions.data).map((group) => {
            const chosen = group.find((c) => c.id === confirmingId)
            return (
            <li key={group[0].id}>
              <small>
                {new Date(group[0].noticed_at).toLocaleString()} —{" "}
                {group.length === 1
                  ? suggestionText(group[0])
                  : ambiguousText(group)}{" "}
                {group.length === 1 ? (
                  <button
                    type="button"
                    className="suggestion-btn"
                    disabled={confirmMove.isPending || dismiss.isPending}
                    onClick={() => {
                      setConfirmingId(group[0].id)
                      setHaulCost("")
                    }}
                  >
                    Yes, this was a move
                  </button>
                ) : (
                  // Picking an origin IS the choice: it opens the same confirm
                  // prompt as a plain card, so the card stays at two controls
                  // no matter how many hangars are candidates.
                  <select
                    className="suggestion-pick"
                    aria-label="Where did it come from?"
                    value={chosen?.id ?? ""}
                    disabled={confirmMove.isPending || dismiss.isPending}
                    onChange={(e) => {
                      setConfirmingId(e.target.value || null)
                      setHaulCost("")
                    }}
                  >
                    <option value="">Where did it come from?…</option>
                    {group.map((c) => (
                      <option key={c.id} value={c.id}>
                        From {candidateOrigin(c)}
                      </option>
                    ))}
                  </select>
                )}{" "}
                <button
                  type="button"
                  className="secondary suggestion-btn"
                  disabled={confirmMove.isPending || dismiss.isPending}
                  onClick={() => dismiss.mutate(group.map((c) => c.id))}
                >
                  Not a move
                </button>
              </small>
              {chosen && costPrompt(chosen.id)}
            </li>
            )
          })}
        </ul>
      )}
      {confirmMove.isError && (
        <p>
          <small className="error">
            {(confirmMove.error as Error).message}
          </small>
        </p>
      )}
      {dismiss.isError && (
        <p>
          <small className="error">{(dismiss.error as Error).message}</small>
        </p>
      )}
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
                {e.kind === "shortfall" && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="linkbtn"
                      onClick={() => onRecordSale(e)}
                    >
                      Record the sale
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

/** Candidate-origin folding (#203): suggestions sharing a `group_id` are the
 * same found stock with more than one plausible origin — one card, and the
 * manager picks which origin to confirm. Ungrouped suggestions stay singleton
 * cards. Order of the list is preserved. */
function groupSuggestions(list: MoveSuggestionOut[]): MoveSuggestionOut[][] {
  const byGroup = new Map<string, MoveSuggestionOut[]>()
  const groups: MoveSuggestionOut[][] = []
  for (const s of list) {
    const existing = s.group_id ? byGroup.get(s.group_id) : undefined
    if (existing) {
      existing.push(s)
      continue
    }
    const fresh = [s]
    if (s.group_id) byGroup.set(s.group_id, fresh)
    groups.push(fresh)
  }
  return groups
}

/** The ambiguous card's question (#203): the same item is missing at more than
 * one hangar and turned up at one — plain English, the manager picks. The
 * per-origin detail (name + how many are missing there) lives in the dropdown
 * options, so the sentence stays short however many hangars are candidates. */
function ambiguousText(group: MoveSuggestionOut[]): string {
  const item = group[0].type_name ?? `Type ${group[0].type_id}`
  const to = group[0].destination_name ?? group[0].destination_location_id
  return `Looks like some ${item} moved to ${to} — it could have come from more than one hangar.`
}

function candidateOrigin(s: MoveSuggestionOut): string {
  const name = s.origin_name ?? s.origin_location_id
  return `${name} (${s.qty.toLocaleString()} missing)`
}

/** A "looks like a move" suggestion (ADR-0049, #200/#206): the same item went
 * missing in one marked hangar and turned up somewhere else — counted in
 * another marked hangar, and/or listed for sale by the buyback wallet (#206).
 * Confirming it (#201) carries what we paid — and how long we've held it — to
 * the new place. */
function suggestionText(s: MoveSuggestionOut): string {
  const item = s.type_name ?? `Type ${s.type_id}`
  const from = s.origin_name ?? s.origin_location_id
  const to = s.destination_name ?? s.destination_location_id
  const base = `Looks like ${s.qty.toLocaleString()} ${item} moved from ${from} to ${to}`
  if (s.qty_listed > 0 && s.qty_listed === s.qty) {
    return `${base} — they're listed for sale there. Was this a move?`
  }
  if (s.qty_listed > 0) {
    return `${base} — ${s.qty_listed.toLocaleString()} of them are listed for sale there. Was this a move?`
  }
  return `${base} — was this a move?`
}

function eventText(e: ReconciliationEventOut): string {
  const item = e.type_name ?? `Type ${e.type_id}`
  const where = e.location_name ?? e.location_id
  const qty = e.qty.toLocaleString()
  if (e.kind === "reprocess_hint") {
    return `Looks like ${qty} ${item} at ${where} was turned into minerals — record it to carry what we paid into them.`
  }
  if (e.kind === "unmatched_sale") {
    return `Sold ${qty} ${item} at ${where} the books didn't have — recorded at estimated value.`
  }
  if (e.kind === "unexpected_division") {
    return `A sell order for ${qty} ${item} at ${where} pays into a different corp wallet than the buyback's.`
  }
  if (e.kind === "move_confirmed") {
    // The note carries where it went and who confirmed, in plain words.
    return `Moved ${qty} ${item} out of ${where}${e.note ? ` — ${e.note}` : ""}.`
  }
  if (e.kind === "move_dismissed") {
    return `Decided ${qty} ${item} didn't move from ${where} — everything stays as recorded.`
  }
  if (e.kind === "move_withdrawn") {
    return `The move hint for ${qty} ${item} from ${where} no longer adds up — it was taken back.`
  }
  if (e.kind === "shipment_recorded") {
    // The note carries where it's headed and who sent it, in plain words.
    return `Sent ${qty} ${item} from ${where}${e.note ? ` — ${e.note}` : ""}.`
  }
  if (e.kind === "shipment_arrived") {
    // The note carries where it came from and who marked it, in plain words.
    return `${qty} ${item} arrived at ${where}${e.note ? ` — ${e.note}` : ""}.`
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
  const names = useDivisionNames()
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
              {h.location_name} —{" "}
              {names.hangar.get(h.division) ?? `hangar ${h.division}`}{" "}
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
                {names.hangar.get(d) ?? `Hangar ${d}`}
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

/** Declared hauls (ADR-0049, #208): record a move between hangars before the
 * hangar check notices it. While a haul is on the road neither hangar's check
 * cries wolf over it; "It arrived" lands the stock at the destination with
 * what we paid — and how long we've held it — intact. Plain English only. */
function HaulsSection() {
  const queryClient = useQueryClient()
  const shipments = useQuery({ queryKey: ["shipments"], queryFn: listShipments })
  const hangars = useQuery({ queryKey: ["hangars"], queryFn: listHangars })
  const [typeQuery, setTypeQuery] = useState("")
  const [picked, setPicked] = useState<{ id: number; name: string } | null>(null)
  const [qty, setQty] = useState(0)
  const [fromId, setFromId] = useState("")
  const [toId, setToId] = useState("")
  const [feedback, setFeedback] = useState<string | null>(null)
  const search = useQuery({
    queryKey: ["typeSearch", typeQuery],
    queryFn: () => searchTypes(typeQuery),
    enabled: typeQuery.length >= 2,
  })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["shipments"] })
    void queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
    void queryClient.invalidateQueries({ queryKey: ["inventory"] })
  }
  const record = useMutation({
    mutationFn: () =>
      recordShipment({
        type_id: picked!.id,
        qty,
        origin_location_id: fromId,
        destination_location_id: toId,
      }),
    onSuccess: () => {
      invalidate()
      setPicked(null)
      setQty(0)
      record.reset()
      setFeedback("On the road — mark it arrived when it lands.")
    },
  })
  const arrived = useMutation({
    mutationFn: (id: string) => markShipmentArrived(id),
    onSuccess: invalidate,
  })

  // Hangars can share a location across divisions — the ends of a haul are
  // locations, so offer each one once.
  const stops = new Map(
    (hangars.data ?? []).map((h) => [h.location_id, h.location_name]),
  )
  const ready = picked && qty > 0 && fromId && toId && fromId !== toId

  return (
    <section className="panel">
      <h2>Hauls between hangars</h2>
      <p>
        <small className="field-hint">
          Moving stock to another hangar? Record the haul here first and the
          hangar checks won&apos;t flag it as missing on one end or found on the
          other. When it lands, mark it arrived — what we paid, and how long
          we&apos;ve held it, move with it.
        </small>
      </p>
      {shipments.data && shipments.data.length > 0 && (
        <ul className="recon-list">
          {shipments.data.map((s) => (
            <li key={s.id}>
              <small>
                {haulText(s)}{" "}
                <button
                  type="button"
                  className="linkbtn"
                  disabled={arrived.isPending}
                  onClick={() => arrived.mutate(s.id)}
                >
                  It arrived
                </button>
              </small>
            </li>
          ))}
        </ul>
      )}
      {arrived.isError && (
        <p>
          <small className="error">{(arrived.error as Error).message}</small>
        </p>
      )}
      {stops.size < 2 ? (
        <p>
          <small className="field-hint">
            Hauls run between two marked hangars — mark a second one above
            first.
          </small>
        </p>
      ) : (
        <>
          <div className="access-actions">
            {picked ? (
              <span>
                {picked.name || `Type ${picked.id}`}{" "}
                <button
                  type="button"
                  className="linkbtn"
                  onClick={() => setPicked(null)}
                >
                  Change item
                </button>
              </span>
            ) : (
              <label>
                Item
                <input
                  type="search"
                  placeholder="Search items to haul…"
                  value={typeQuery}
                  onChange={(e) => setTypeQuery(e.target.value)}
                />
                {(search.data ?? []).length > 0 && (
                  <ul className="search-results">
                    {(search.data ?? []).slice(0, 8).map((t) => (
                      <li key={t.type_id}>
                        <button
                          type="button"
                          className="linkbtn"
                          onClick={() => {
                            setPicked({ id: t.type_id, name: t.name })
                            setTypeQuery("")
                          }}
                        >
                          {t.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </label>
            )}
            <label>
              How many
              <input
                type="number"
                min={1}
                value={qty || ""}
                onChange={(e) => setQty(Number(e.target.value))}
              />
            </label>
            <select
              aria-label="From hangar"
              value={fromId}
              onChange={(e) => setFromId(e.target.value)}
            >
              <option value="">From…</option>
              {[...stops].map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
            <select
              aria-label="To hangar"
              value={toId}
              onChange={(e) => setToId(e.target.value)}
            >
              <option value="">To…</option>
              {[...stops].map(([id, name]) => (
                <option key={id} value={id} disabled={id === fromId}>
                  {name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="secondary"
              disabled={!ready || record.isPending}
              onClick={() => record.mutate()}
            >
              {record.isPending ? "Recording…" : "Record the haul"}
            </button>
          </div>
          {feedback && (
            <p>
              <small className="field-hint" role="status">
                {feedback}
              </small>
            </p>
          )}
          {record.isError && (
            <p>
              <small className="error">{(record.error as Error).message}</small>
            </p>
          )}
        </>
      )}
    </section>
  )
}

/** One open haul, in plain words: what's on the road, from where to where,
 * since when. */
function haulText(s: ShipmentOut): string {
  const item = s.type_name ?? `Type ${s.type_id}`
  const from = s.origin_name ?? s.origin_location_id
  const to = s.destination_name ?? s.destination_location_id
  const sent = new Date(s.sent_at).toLocaleDateString()
  return `${s.qty.toLocaleString()} ${item} on its way from ${from} to ${to} (sent ${sent}).`
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
            item.lots.length === 1 &&
            item.reprocessable && (
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
                {lot.source === "manual" && (
                  <>
                    {" "}
                    <StatusChip variant="muted">Entered by hand</StatusChip>
                  </>
                )}
                {lot.source === "reprocess" && (
                  <>
                    {" "}
                    <StatusChip variant="muted">From reprocessing</StatusChip>
                  </>
                )}
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
              {item.reprocessable && (
                <button
                  type="button"
                  className="linkbtn"
                  onClick={() => onReprocess(lot.id)}
                >
                  <small>Turned into minerals</small>
                </button>
              )}
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
