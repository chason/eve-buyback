import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { getInventory } from "../api/accounting"
import type { InventoryItemOut } from "../api/accounting"
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

  const inv = result.data.inventory
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
                <th>Sitting for</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {inv.items.map((item) => (
                <ItemRows key={item.type_id} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
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

function ItemRows({ item }: { item: InventoryItemOut }) {
  const [open, setOpen] = useState(false)
  const multiple = item.lots.length > 1
  return (
    <>
      <tr>
        <td>
          {item.type_name ?? `Type ${item.type_id}`}
          {item.any_estimated && (
            <>
              {" "}
              <StatusChip variant="info">Estimated value</StatusChip>
            </>
          )}
        </td>
        <td className="num">{item.qty.toLocaleString()}</td>
        <td className="num isk" title={formatIsk(item.total_cost)}>
          {formatIskCompact(item.total_cost)}
        </td>
        <td>
          {daysText(item.oldest_days)}
          {item.stale && (
            <>
              {" "}
              <StatusChip variant="danger">Sitting a while</StatusChip>
            </>
          )}
        </td>
        <td>
          {multiple && (
            <button
              type="button"
              className="linkbtn"
              aria-expanded={open}
              onClick={() => setOpen(!open)}
            >
              {open ? "Hide" : `${item.lots.length} buys`}
            </button>
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
                {lot.cost_is_estimated && (
                  <>
                    {" "}
                    <StatusChip variant="info">Estimated value</StatusChip>
                  </>
                )}
              </small>
            </td>
            <td className="num">
              <small>{lot.qty.toLocaleString()}</small>
            </td>
            <td className="num isk">
              <small title={formatIsk(lot.total_cost)}>
                {formatIsk(lot.unit_cost)} each
              </small>
            </td>
            <td>
              <small>
                {daysText(lot.days_held)}
                {lot.stale && (
                  <>
                    {" "}
                    <StatusChip variant="danger">Sitting a while</StatusChip>
                  </>
                )}
              </small>
            </td>
            <td />
          </tr>
        ))}
    </>
  )
}

function daysText(days: number): string {
  if (days === 0) return "today"
  if (days === 1) return "1 day"
  return `${days.toLocaleString()} days`
}
