import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { listAppraisals } from "../api/appraisals"
import { getMe } from "../api/auth"
import { ContractStatusChip } from "../components/ContractStatusChip"
import { StatusChip } from "../components/StatusChip"
import { formatIsk } from "../lib/format"
import { isManager } from "../lib/roles"

export default function History() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const appraisals = useQuery({
    queryKey: ["appraisals"],
    queryFn: listAppraisals,
  })
  const corpWide = isManager(me.data?.role)

  if (appraisals.isLoading) return <p aria-busy="true">Loading…</p>
  if (appraisals.isError || !appraisals.data) {
    return <p className="error">Could not load appraisals.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Appraisals</h1>
        <p>{corpWide ? "Everyone in your corporation." : "Your appraisals."}</p>
      </hgroup>

      {appraisals.data.length === 0 ? (
        <p>
          No appraisals yet. <Link to="/appraise">Create one</Link>.
        </p>
      ) : (
        <div className="panel">
        <table>
          <thead>
            <tr>
              <th>When</th>
              {corpWide && <th>By</th>}
              <th>Drop-off</th>
              <th>Accepted</th>
              <th>Rejected</th>
              <th>Contract</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {appraisals.data.map((a) => (
              <tr key={a.public_id}>
                <td>{new Date(a.created_at).toLocaleString()}</td>
                {corpWide && (
                  <td>{a.created_by_character_name ?? a.created_by_character_id}</td>
                )}
                <td>{a.delivery_location_name ?? "—"}</td>
                <td className="num isk">{formatIsk(a.accepted_total)}</td>
                <td className="num">
                  {a.rejected_count > 0 ? (
                    <StatusChip variant="danger">{a.rejected_count}</StatusChip>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <ContractStatusChip status={a.contract_status} />
                </td>
                <td>
                  <Link to={`/a/${a.public_id}`}>View</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </>
  )
}
