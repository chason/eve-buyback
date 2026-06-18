import type { ReactNode } from "react"

// HUD telemetry chip (#44, #83): a bordered, mono, uppercase pill. The variant only
// picks a color — border + tint derive from it via currentColor (see `.status` in
// index.css). Used for line/connection states and now Rules flags, the Locations
// kind, and the History rejected count, so data-heavy tables read like a console.
export type StatusVariant =
  | "accepted" // green — yes / on / accepted
  | "rejected" // red — a rejected appraisal line
  | "danger" // red — a count/state that warrants attention
  | "info" // cyan — an active categorical flag (reprocess, structure)
  | "muted" // grey — off / inactive / neutral
  | "online"
  | "offline"
  | "expired"

export function StatusChip({
  variant,
  children,
}: {
  variant: StatusVariant
  children: ReactNode
}) {
  return <span className={`status status--${variant}`}>{children}</span>
}
