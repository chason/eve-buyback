import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type { CorpAccessOut, PaymentOut } from "../api/admin"
import {
  beginWalletAuthorize,
  getBillingSettings,
  getWalletStatus,
  grantCorpAccess,
  listCorpAccess,
  listPayments,
  matchPayment,
  revokeCorpAccess,
  revokeWallet,
  updateBillingSettings,
} from "../api/admin"
import { getMe } from "../api/auth"
import { ConfirmButton } from "../components/ConfirmButton"
import { StatusChip, type StatusVariant } from "../components/StatusChip"
import { formatIsk } from "../lib/format"

/** What the access cell says, in plain English (no billing/entitlement jargon) —
 * rendered as the same HUD status chips the appraisal line states use. */
function accessChip(corp: CorpAccessOut): { label: string; variant: StatusVariant } {
  if (corp.active) return { label: "On", variant: "accepted" }
  if (corp.granted_at) return { label: "Expired", variant: "expired" }
  return { label: "Off", variant: "muted" }
}

function untilLabel(corp: CorpAccessOut): string {
  if (!corp.granted_at) return "—"
  if (!corp.expires_at) return "Forever"
  // EVE runs on UTC; render the expiry in UTC so it matches the date the admin picked.
  return new Date(corp.expires_at).toLocaleDateString(undefined, {
    timeZone: "UTC",
  })
}

function sourceLabel(corp: CorpAccessOut): string | undefined {
  if (!corp.granted_at) return undefined
  return corp.source === "payment" ? "paid" : "granted by admin"
}

/** Today in EVE/UTC as YYYY-MM-DD — the floor for grant end dates. */
function todayUtc(): string {
  return new Date().toISOString().slice(0, 10)
}

/** Empty = perpetual; otherwise a complete ISO date that isn't in the past.
 * String comparison is safe for YYYY-MM-DD. */
function isValidUntil(until: string): boolean {
  if (until === "") return true
  return /^\d{4}-\d{2}-\d{2}$/.test(until) && until >= todayUtc()
}

function AccessRow({ corp }: { corp: CorpAccessOut }) {
  const queryClient = useQueryClient()
  // Optional "until" date for the grant; empty = access never expires.
  const [until, setUntil] = useState("")

  const grant = useMutation({
    mutationFn: () =>
      grantCorpAccess(
        corp.corporation_id,
        // A picked date means "through that day" (end of day, EVE/UTC time).
        until ? `${until}T23:59:59Z` : null,
      ),
    onSuccess: () => {
      setUntil("")
      void queryClient.invalidateQueries({ queryKey: ["corpAccess"] })
    },
  })
  const revoke = useMutation({
    mutationFn: () => revokeCorpAccess(corp.corporation_id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["corpAccess"] }),
  })

  // How the grant came to be shows on hover, not inline — the chip stays clean.
  const source = sourceLabel(corp)
  const chip = accessChip(corp)
  return (
    <tr>
      <td>{corp.corporation_name}</td>
      <td>
        <span className="access-badge" title={source}>
          <StatusChip variant={chip.variant}>{chip.label}</StatusChip>
        </span>
      </td>
      <td>{untilLabel(corp)}</td>
      <td className="access-actions">
        <input
          type="text"
          className="access-date"
          placeholder="YYYY-MM-DD"
          maxLength={10}
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          aria-label={`Access until date for ${corp.corporation_name}`}
          aria-invalid={until !== "" && !isValidUntil(until) ? true : undefined}
          title="End date in EVE time (YYYY-MM-DD), today or later; leave empty for access that never expires"
        />
        <button
          type="button"
          className="secondary access-grant-btn"
          disabled={grant.isPending || !isValidUntil(until)}
          onClick={() => grant.mutate()}
        >
          {corp.active ? "Update access" : "Give access"}
        </button>
        {corp.granted_at != null && (
          <ConfirmButton
            className="linkbtn"
            label="Remove"
            title="Remove access?"
            prompt="The corporation will lose access to the paid features."
            confirmLabel="Remove access"
            onConfirm={() => revoke.mutate()}
          />
        )}
        {grant.isError && (
          <p className="error">{(grant.error as Error).message}</p>
        )}
        {revoke.isError && (
          <p className="error">{(revoke.error as Error).message}</p>
        )}
      </td>
    </tr>
  )
}

function PaymentRow({
  payment,
  corps,
}: {
  payment: PaymentOut
  corps: CorpAccessOut[]
}) {
  const queryClient = useQueryClient()
  const [corpId, setCorpId] = useState("")
  const apply = useMutation({
    mutationFn: () => matchPayment(payment.id, Number(corpId)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["payments"] })
      void queryClient.invalidateQueries({ queryKey: ["corpAccess"] })
    },
  })
  return (
    <tr>
      <td>{new Date(payment.received_at).toLocaleDateString()}</td>
      <td>{payment.sender_name ?? "Unknown"}</td>
      <td className="num isk">{formatIsk(payment.amount)}</td>
      <td>{payment.reason || <span className="field-hint">no message</span>}</td>
      <td>
        {payment.matched ? (
          <>
            {payment.matched_corporation_name}
            {payment.periods_granted > 0 && (
              <>
                {" "}
                <small className="field-hint">
                  (+{payment.periods_granted * 30} days)
                </small>
              </>
            )}
          </>
        ) : (
          <span className="access-actions">
            <select
              value={corpId}
              onChange={(e) => setCorpId(e.target.value)}
              aria-label={`Corporation for payment of ${formatIsk(payment.amount)}`}
            >
              <option value="">Pick a corporation…</option>
              {corps.map((c) => (
                <option key={c.corporation_id} value={c.corporation_id}>
                  {c.corporation_name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="secondary"
              disabled={!corpId || apply.isPending}
              onClick={() => apply.mutate()}
            >
              Apply
            </button>
            {apply.isError && (
              <p className="error">{(apply.error as Error).message}</p>
            )}
          </span>
        )}
      </td>
    </tr>
  )
}

function PriceEditor({ priceIsk, periodDays }: { priceIsk: number; periodDays: number }) {
  const queryClient = useQueryClient()
  const [price, setPrice] = useState(String(priceIsk))
  const save = useMutation({
    mutationFn: () => updateBillingSettings(Number(price)),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["billingSettings"] }),
  })
  const parsed = Number(price)
  const valid = Number.isInteger(parsed) && parsed > 0

  return (
    <p className="access-actions">
      <small className="field-hint">
        Access costs <strong>{formatIsk(String(priceIsk))}</strong> per{" "}
        {periodDays} days.
      </small>{" "}
      <input
        type="number"
        min={1}
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        aria-label="Access price in ISK"
        title="ISK per access period"
      />
      <button
        type="button"
        className="secondary"
        disabled={!valid || Number(price) === priceIsk || save.isPending}
        onClick={() => save.mutate()}
      >
        Save price
      </button>
      {save.isError && <span className="error">{(save.error as Error).message}</span>}
    </p>
  )
}

function PaymentsSection({ corps }: { corps: CorpAccessOut[] }) {
  const queryClient = useQueryClient()
  const billing = useQuery({
    queryKey: ["billingSettings"],
    queryFn: getBillingSettings,
  })
  const wallet = useQuery({ queryKey: ["walletStatus"], queryFn: getWalletStatus })
  const payments = useQuery({
    queryKey: ["payments"],
    queryFn: () => listPayments(),
    enabled: !!wallet.data?.connected,
  })
  const connect = useMutation({
    mutationFn: beginWalletAuthorize,
    onSuccess: (url) => window.location.assign(url),
  })
  const disconnect = useMutation({
    mutationFn: revokeWallet,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["walletStatus"] }),
  })

  return (
    <section>
      <h2>Payments</h2>
      {billing.data && (
        <PriceEditor
          key={billing.data.price_isk}
          priceIsk={billing.data.price_isk}
          periodDays={billing.data.period_days}
        />
      )}
      {wallet.data && !wallet.data.connected && (
        <p>
          <small className="field-hint">
            Connect the wallet that receives access payments — the character
            corporations send ISK to. Payments arriving there will unlock access
            automatically.
          </small>{" "}
          <button
            type="button"
            className="secondary"
            disabled={!wallet.data.configured || connect.isPending}
            onClick={() => connect.mutate()}
          >
            Connect payment wallet
          </button>
        </p>
      )}
      {wallet.data?.connected && (
        <p>
          <small className="field-hint">
            Payments to <strong>{wallet.data.character_name}</strong> are checked
            about every 30 minutes.
            {wallet.data.expired &&
              " The connection has expired — reconnect it."}{" "}
            <ConfirmButton
              className="linkbtn"
              label="Disconnect"
              title="Disconnect the payment wallet?"
              prompt="Payments will stop being noticed until it's reconnected."
              confirmLabel="Disconnect wallet"
              onConfirm={() => disconnect.mutate()}
            />
          </small>
        </p>
      )}
      {connect.isError && (
        <p className="error">{(connect.error as Error).message}</p>
      )}

      {payments.data && payments.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>From</th>
              <th>Amount</th>
              <th>Message</th>
              <th>Applied to</th>
            </tr>
          </thead>
          <tbody>
            {payments.data.map((p) => (
              <PaymentRow key={p.id} payment={p} corps={corps} />
            ))}
          </tbody>
        </table>
      )}
      {wallet.data?.connected && payments.data?.length === 0 && (
        <p>
          <small className="field-hint">No payments seen yet.</small>
        </p>
      )}
    </section>
  )
}

export default function Admin() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const isAdmin = !!me.data?.is_app_admin

  const access = useQuery({
    queryKey: ["corpAccess"],
    queryFn: listCorpAccess,
    enabled: isAdmin,
  })

  if (me.data && !isAdmin) {
    return <p className="error">Only an app admin can manage access.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Admin</h1>
        <p>
          Give corporations access to the paid features, or take it away. Access
          with no date lasts forever; paid access renews when a payment arrives.
        </p>
      </hgroup>

      {access.isLoading && <p aria-busy="true">Loading corporations…</p>}
      {access.isError && (
        <p className="error">{(access.error as Error).message}</p>
      )}

      {access.data &&
        (access.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Corporation</th>
                <th>Access</th>
                <th>Until</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {access.data.map((corp) => (
                <AccessRow key={corp.corporation_id} corp={corp} />
              ))}
            </tbody>
          </table>
        ) : (
          <p>
            <small className="field-hint">No corporations registered yet.</small>
          </p>
        ))}

      <PaymentsSection corps={access.data ?? []} />
    </>
  )
}
