import { StatusChip, type StatusVariant } from "./StatusChip"

// The matched-contract status surfaced on an appraisal (ADR-0037). `mismatch` warns that a
// contract cites the appraisal but its items/price/location don't match it.
const LABELS: Record<
  string,
  { label: string; variant: StatusVariant; title?: string }
> = {
  in_progress: { label: "In Progress", variant: "info" },
  completed: { label: "Completed", variant: "accepted" },
  mismatch: {
    label: "Mismatch",
    variant: "danger",
    title:
      "A contract cites this appraisal but its items, price, or location don't match — don't accept it without checking.",
  },
  rejected: { label: "Rejected", variant: "danger" },
  cancelled: { label: "Cancelled", variant: "muted" },
  expired: { label: "Expired", variant: "muted" },
  failed: { label: "Failed", variant: "danger" },
}

export function ContractStatusChip({
  status,
}: {
  status: string | null | undefined
}) {
  const match = status ? LABELS[status] : undefined
  if (!match) return <>—</>
  return (
    <span title={match.title}>
      <StatusChip variant={match.variant}>{match.label}</StatusChip>
    </span>
  )
}
