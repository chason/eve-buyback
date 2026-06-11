import { useQuery } from "@tanstack/react-query"
import { Fragment, useState } from "react"
import { useParams } from "react-router-dom"

import { getAppraisal } from "../api/appraisals"
import { getMe } from "../api/auth"
import { formatIsk } from "../lib/format"
import { hubName } from "../lib/hubs"

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
        className="secondary outline"
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
        <strong className="isk">{formatIsk(a.accepted_total)}</strong> accepted
        {a.rejected_count > 0 && ` · ${a.rejected_count} rejected`}
      </p>
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
                <td>{line.status === "accepted" ? "✓" : line.reason}</td>
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
    </>
  )
}
