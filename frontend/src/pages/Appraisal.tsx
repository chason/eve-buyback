import { useQuery } from "@tanstack/react-query"
import { Fragment, useEffect, useState } from "react"
import { useParams } from "react-router-dom"

import { getAppraisal } from "../api/appraisals"
import { getMe } from "../api/auth"
import { ContractStatusChip } from "../components/ContractStatusChip"
import { StatusChip } from "../components/StatusChip"
import { formatIsk } from "../lib/format"
import { hubName } from "../lib/hubs"

/** Whether the user (or environment) prefers reduced motion. Defaults to `true` when
 *  `matchMedia` is unavailable (jsdom/SSR/old browsers) so motion-guarded UI degrades
 *  to its static form rather than animating blindly. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return true
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches
  })
  useEffect(() => {
    if (!window.matchMedia) return
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [])
  return reduced
}

/** Reveals an ISK figure with a typewriter effect (#49) — a small flourish on the
 *  appraisal total. Honors prefers-reduced-motion (shows the value at once), keeps the
 *  full value accessible via aria-label, and reserves the final width (a hidden ghost)
 *  so neighbouring text doesn't shift as it types. */
function TypewriterIsk({ value }: { value: string }) {
  const reduced = usePrefersReducedMotion()
  const [shown, setShown] = useState(reduced ? value : "")

  useEffect(() => {
    if (reduced) {
      setShown(value)
      return
    }
    setShown("")
    let i = 0
    const id = window.setInterval(() => {
      i += 1
      setShown(value.slice(0, i))
      if (i >= value.length) window.clearInterval(id)
    }, 35)
    return () => window.clearInterval(id)
  }, [value, reduced])

  const typing = !reduced && shown.length < value.length
  return (
    <strong className="isk typewriter" aria-label={value}>
      <span className="typewriter-ghost" aria-hidden="true">
        {value}
      </span>
      <span className="typewriter-text" aria-hidden="true">
        {shown}
        {typing && <span className="type-caret">▋</span>}
      </span>
    </strong>
  )
}

export default function Appraisal() {
  const { publicId } = useParams<{ publicId: string }>()
  const appraisal = useQuery({
    queryKey: ["appraisal", publicId],
    queryFn: () => getAppraisal(publicId!),
    enabled: !!publicId,
  })
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  // Tracks which field was just copied (or "<key>:failed"), cleared after 2s.
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  async function copy(key: string, text: string) {
    // navigator.clipboard is undefined in non-secure contexts (plain http,
    // non-localhost); guard so the click never rejects unhandled.
    try {
      await navigator.clipboard.writeText(text)
      setCopiedKey(key)
    } catch {
      setCopiedKey(`${key}:failed`)
    }
    window.setTimeout(() => setCopiedKey(null), 2000)
  }

  function CopyButton({ fieldKey, text }: { fieldKey: string; text: string }) {
    const label =
      copiedKey === fieldKey
        ? "Copied"
        : copiedKey === `${fieldKey}:failed`
          ? "Failed"
          : "Copy"
    return (
      <button
        type="button"
        className="secondary outline copy-btn"
        onClick={() => copy(fieldKey, text)}
      >
        {label}
      </button>
    )
  }

  if (appraisal.isLoading) return <p aria-busy="true">Loading…</p>
  if (appraisal.isError || !appraisal.data) {
    return <p className="error">Appraisal not found.</p>
  }
  const a = appraisal.data
  // A successfully loaded appraisal is always the viewer's own corp (cross-corp
  // reads 404), so the session's corp name is the contract recipient.
  const entity = me.data?.corporation_name ?? "your corporation"
  const hasPayout = Number(a.accepted_total) > 0

  return (
    <>
      <hgroup>
        <h1>Appraisal</h1>
        <p>
          {new Date(a.created_at).toLocaleString()} · {hubName(a.market_hub_id)}
          {a.created_by_character_name && ` · by ${a.created_by_character_name}`}
        </p>
      </hgroup>

      <p>
        <TypewriterIsk value={formatIsk(a.accepted_total)} /> accepted
        {a.rejected_count > 0 && ` · ${a.rejected_count} rejected`}
      </p>
      {a.contract_status && (
        <p>
          Contract: <ContractStatusChip status={a.contract_status} />
        </p>
      )}
      {a.delivery_location_name && (
        <p>
          Drop-off: <strong>{a.delivery_location_name}</strong>
        </p>
      )}
      <button
        className="secondary"
        onClick={() => copy("link", window.location.href)}
      >
        {copiedKey === "link"
          ? "Link copied"
          : copiedKey === "link:failed"
            ? "Copy failed — copy from the address bar"
            : "Copy link"}
      </button>

      {hasPayout && (
        <article>
          <header>Get paid — create the contract</header>
          <p>
            In EVE, open <strong>Contracts → Create Contract → Item Exchange</strong>{" "}
            and enter these:
          </p>
          <table>
            <tbody>
              <tr>
                <td>Contract to</td>
                <td>
                  <strong>{entity}</strong>{" "}
                  <small>— your corporation</small>
                </td>
                <td>
                  <CopyButton fieldKey="entity" text={entity} />
                </td>
              </tr>
              <tr>
                <td>I will receive</td>
                <td>
                  <strong className="isk">{formatIsk(a.accepted_total)}</strong>
                </td>
                <td>
                  {/* Copy the raw value (no separators/“ISK”) for EVE's field. */}
                  <CopyButton fieldKey="amount" text={a.accepted_total} />
                </td>
              </tr>
              <tr>
                <td>Description</td>
                <td>
                  <code>{a.public_id}</code>{" "}
                  <small>— so a manager can look it up</small>
                </td>
                <td>
                  <CopyButton fieldKey="id" text={a.public_id} />
                </td>
              </tr>
            </tbody>
          </table>
          <p>
            <small>Then wait for a Buyback Manager to accept it.</small>
          </p>
        </article>
      )}

      <div className="panel">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Qty</th>
            <th>Basis</th>
            <th>%</th>
            <th>Unit</th>
            <th>Total</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {a.lines.map((line, idx) => (
            <Fragment key={`${line.type_id}-${idx}`}>
              <tr className={line.status === "rejected" ? "rejected" : undefined}>
                <td>{line.type_name}</td>
                <td className="num">{line.quantity.toLocaleString()}</td>
                <td>
                  {line.basis ?? "—"}
                  {line.market_hub_id && (
                    <>
                      {" "}
                      <small>
                        @ {line.market_hub_name ?? hubName(line.market_hub_id)}
                      </small>
                    </>
                  )}
                </td>
                <td className="num">{line.percentage ?? "—"}</td>
                <td className="num isk">
                  {line.unit_price ? formatIsk(line.unit_price) : "—"}
                </td>
                <td className="num isk">{formatIsk(line.line_total)}</td>
                <td>
                  {line.status === "accepted" ? (
                    <StatusChip variant="accepted">Accepted</StatusChip>
                  ) : (
                    <>
                      <StatusChip variant="rejected">Rejected</StatusChip>
                      {line.reason && (
                        <small className="status-reason">{line.reason}</small>
                      )}
                    </>
                  )}
                </td>
              </tr>
              {line.reprocess && (
                <tr className="reprocess-detail">
                  <td colSpan={7}>
                    <small>
                      ♻ Reprocessed into:{" "}
                      {line.reprocess.minerals.map((m, i) => (
                        <span key={m.type_id}>
                          {i > 0 && ", "}
                          {m.type_name} ×
                          {Number(m.quantity).toLocaleString(undefined, {
                            maximumFractionDigits: 0,
                          })}{" "}
                          = {formatIsk(m.value)}
                        </span>
                      ))}
                      {line.reprocess.leftover_units > 0 &&
                        ` · ${line.reprocess.leftover_units.toLocaleString()} units at ore price (${formatIsk(
                          line.reprocess.leftover_value,
                        )})`}
                    </small>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      </div>
    </>
  )
}
