import { useQuery } from "@tanstack/react-query"

import { getAccountingAccess } from "../api/billing"
import { formatIsk } from "../lib/format"

/** The manager-facing "paid features" panel (ADR-0042): whether the corp has access
 * to the accounting add-on and — when the instance takes payments — how to pay, in
 * plain English. Renders nothing while loading or for corps without the endpoint. */
export default function AccountingAccessPanel() {
  const access = useQuery({
    queryKey: ["accountingAccess"],
    queryFn: getAccountingAccess,
  })
  const a = access.data
  if (!a) return null

  return (
    <section className="panel">
      <h2>Paid features</h2>
      {a.active ? (
        <p>
          Your corporation has access to the accounting add-on
          {a.expires_at ? (
            <>
              {" "}
              until{" "}
              <strong>
                {new Date(a.expires_at).toLocaleDateString(undefined, {
                  timeZone: "UTC",
                })}
              </strong>
              .
            </>
          ) : (
            <> — it never expires.</>
          )}
        </p>
      ) : (
        <p>Your corporation doesn&apos;t have the accounting add-on yet.</p>
      )}
      {a.payment_configured && (!a.active || a.expires_at) && (
        <p>
          <small className="field-hint">
            To {a.active ? "extend" : "get"} access, send{" "}
            <strong>{formatIsk(String(a.price_isk))}</strong> (per {a.period_days}{" "}
            days) to <strong>{a.operator_character_name}</strong> in game, and put{" "}
            <code>{a.reference}</code> in the transfer reason so the payment is
            recognized. Access updates within about half an hour of the ISK
            arriving.
          </small>
        </p>
      )}
    </section>
  )
}
