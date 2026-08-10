import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { getProfit } from "../api/accounting"
import type { ChannelProfitOut, ProfitOut } from "../api/accounting"
import AccountingAccessPanel from "../components/AccountingAccessPanel"
import { StatusChip } from "../components/StatusChip"
import { formatIsk, formatIskCompact } from "../lib/format"

/** The period presets, in plain English. Boundaries are UTC — EVE time. */
const PERIODS = [
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "last_30", label: "Last 30 days" },
  { value: "all", label: "All time" },
] as const

type PeriodChoice = (typeof PERIODS)[number]["value"]

function periodBounds(
  choice: PeriodChoice,
  now: Date,
): { since: string | null; until: string | null } {
  const monthStart = (offset: number) =>
    new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset, 1),
    ).toISOString()
  switch (choice) {
    case "this_month":
      return { since: monthStart(0), until: null }
    case "last_month":
      return { since: monthStart(-1), until: monthStart(0) }
    case "last_30":
      return {
        since: new Date(now.getTime() - 30 * 24 * 3600 * 1000).toISOString(),
        until: null,
      }
    case "all":
      return { since: null, until: null }
  }
}

/** "How we're doing" (ADR-0043/0045, #159): what the buyback made over a period,
 * in plain English — what sales brought in, what the sold stock had cost, what
 * selling cost on top. Estimated-cost results stay visibly apart, never blended. */
export default function Profit() {
  const [period, setPeriod] = useState<PeriodChoice>("this_month")
  const { since, until } = periodBounds(period, new Date())
  const result = useQuery({
    queryKey: ["profit", since, until],
    queryFn: () => getProfit(since, until),
  })

  return (
    <>
      <hgroup>
        <h1>How we&apos;re doing</h1>
        <p>What the buyback made, after everything it cost.</p>
      </hgroup>

      {result.isLoading && <p aria-busy="true">Loading…</p>}
      {(result.isError || (!result.isLoading && !result.data)) && (
        <p className="error">Could not load the numbers.</p>
      )}
      {result.data &&
        (result.data.access ? (
          <ProfitView
            profit={result.data.profit}
            period={period}
            onPeriod={setPeriod}
          />
        ) : (
          <AccountingAccessPanel />
        ))}
    </>
  )
}

function ProfitView({
  profit,
  period,
  onPeriod,
}: {
  profit: ProfitOut
  period: PeriodChoice
  onPeriod: (p: PeriodChoice) => void
}) {
  const down = profit.profit.startsWith("-")
  const nothingYet = profit.sale_count === 0 && isZero(profit.write_downs)
  return (
    <>
      <label className="profit-period">
        Show{" "}
        <select
          value={period}
          onChange={(e) => onPeriod(e.target.value as PeriodChoice)}
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {nothingYet ? (
        <p>
          Nothing sold in this period yet. Sales show up here as the app spots
          them — market sales, contracts, and anything recorded by hand.
        </p>
      ) : (
        <>
          <div className="inventory-summary">
            <div className="inventory-card">
              <small>{down ? "We're down" : "We made"}</small>
              <strong
                className={down ? "isk worth-loss" : "isk"}
                title={formatIsk(profit.profit)}
              >
                {formatIskCompact(stripSign(profit.profit))} ISK
              </strong>
              <small>
                {profit.sale_count === 1
                  ? "from 1 sale"
                  : `from ${profit.sale_count} sales`}
              </small>
            </div>
          </div>

          <div className="panel">
            <table>
              <tbody>
                <BreakdownRow label="Sales brought in" value={profit.revenue} />
                <BreakdownRow
                  label="The stock we sold had cost us"
                  value={profit.cost_of_goods}
                  spend
                />
                <BreakdownRow
                  label="Sales tax"
                  value={profit.sales_tax}
                  spend
                  hideWhenZero
                />
                <BreakdownRow
                  label="Broker fees"
                  value={profit.fees}
                  spend
                  hideWhenZero
                />
                <BreakdownRow
                  label="Stock marked down to match the market"
                  value={profit.write_downs}
                  spend
                  hideWhenZero
                />
                <BreakdownRow
                  label="Other costs"
                  value={profit.other_expenses}
                  spend
                  hideWhenZero
                />
                <tr className="profit-total">
                  <th scope="row">{down ? "Lost" : "Made"}</th>
                  <td className="num isk">
                    <span title={formatIsk(profit.profit)}>
                      {formatIskCompact(profit.profit)}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
            {!isZero(profit.estimated_margin) && (
              <small className="field-hint">
                <StatusChip variant="info">Estimated value</StatusChip>{" "}
                {formatIskCompact(profit.estimated_margin)} ISK of this comes
                from stock whose cost was estimated, not known exactly.
              </small>
            )}
          </div>

          <ChannelsTable channels={profit.channels} />
        </>
      )}
    </>
  )
}

function BreakdownRow({
  label,
  value,
  spend = false,
  hideWhenZero = false,
}: {
  label: string
  value: string
  spend?: boolean
  hideWhenZero?: boolean
}) {
  if (hideWhenZero && isZero(value)) return null
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num isk">
        <span title={formatIsk(value)}>
          {spend && !isZero(value) ? "−" : ""}
          {formatIskCompact(value)}
        </span>
      </td>
    </tr>
  )
}

const CHANNEL_LABELS: Record<string, string> = {
  market: "Market sales",
  contract: "Contracts",
  direct: "Deals recorded by hand",
}

function ChannelsTable({ channels }: { channels: ChannelProfitOut[] }) {
  const active = channels.filter(
    (c) => c.sale_count > 0 || !isZero(c.revenue),
  )
  if (active.length === 0) return null
  return (
    <section>
      <h2>Where it came from</h2>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th className="num">Sales</th>
              <th className="num">Brought in</th>
              <th className="num">Cost us</th>
              <th className="num">Made</th>
            </tr>
          </thead>
          <tbody>
            {active.map((c) => (
              <tr key={c.channel}>
                <td>{CHANNEL_LABELS[c.channel] ?? c.channel}</td>
                <td className="num">{c.sale_count.toLocaleString()}</td>
                <td className="num isk">
                  <span title={formatIsk(c.revenue)}>
                    {formatIskCompact(c.revenue)}
                  </span>
                </td>
                <td className="num isk">
                  <span title={formatIsk(c.cost_of_goods)}>
                    {formatIskCompact(c.cost_of_goods)}
                  </span>
                </td>
                <td className="num isk">
                  <span
                    className={c.margin.startsWith("-") ? "worth-loss" : undefined}
                    title={formatIsk(c.margin)}
                  >
                    {formatIskCompact(c.margin)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Zero test for a Decimal string ("0", "0.00", "0E+29") without Number() float
 * risk on huge values — any digit 1-9 anywhere means nonzero. */
function isZero(value: string): boolean {
  return !/[1-9]/.test(value)
}

function stripSign(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value
}
