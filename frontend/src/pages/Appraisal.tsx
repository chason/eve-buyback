import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useParams } from "react-router-dom"

import { getAppraisal } from "../api/appraisals"
import { formatIsk } from "../lib/format"

export default function Appraisal() {
  const { publicId } = useParams<{ publicId: string }>()
  const appraisal = useQuery({
    queryKey: ["appraisal", publicId],
    queryFn: () => getAppraisal(publicId!),
    enabled: !!publicId,
  })
  const [copied, setCopied] = useState(false)

  if (appraisal.isLoading) return <p aria-busy="true">Loading…</p>
  if (appraisal.isError || !appraisal.data) {
    return <p className="error">Appraisal not found.</p>
  }
  const a = appraisal.data

  async function copyLink() {
    await navigator.clipboard.writeText(window.location.href)
    setCopied(true)
  }

  return (
    <>
      <hgroup>
        <h1>Appraisal</h1>
        <p>
          {new Date(a.created_at).toLocaleString()} · hub {a.market_hub_id}
        </p>
      </hgroup>

      <p>
        <strong className="isk">{formatIsk(a.accepted_total)}</strong> accepted
        {a.rejected_count > 0 && ` · ${a.rejected_count} rejected`}
      </p>
      <button className="secondary" onClick={copyLink}>
        {copied ? "Link copied" : "Copy link"}
      </button>

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
            <tr
              key={idx}
              className={line.status === "rejected" ? "rejected" : undefined}
            >
              <td>{line.type_name}</td>
              <td className="num">{line.quantity.toLocaleString()}</td>
              <td>{line.basis ?? "—"}</td>
              <td className="num">{line.percentage ?? "—"}</td>
              <td className="num isk">
                {line.unit_price ? formatIsk(line.unit_price) : "—"}
              </td>
              <td className="num isk">{formatIsk(line.line_total)}</td>
              <td>{line.status === "accepted" ? "✓" : line.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
