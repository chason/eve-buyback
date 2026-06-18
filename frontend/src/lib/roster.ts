// Manual roster-refresh cooldown, mirrored from the backend default
// (roster_manual_refresh_min_interval_seconds = 900s, ADR-0036). The server is the
// real enforcer (429); this just disables the button + shows a countdown. If an
// operator overrides the setting the estimate may be off, but the 429 still surfaces.
export const ROSTER_REFRESH_COOLDOWN_MS = 15 * 60 * 1000

/** Milliseconds until a manual roster refresh is allowed again (0 = allowed now). */
export function refreshCooldownRemaining(
  syncedAt: string | null | undefined,
  now: number = Date.now(),
): number {
  if (!syncedAt) return 0
  const elapsed = now - new Date(syncedAt).getTime()
  return Math.max(0, ROSTER_REFRESH_COOLDOWN_MS - elapsed)
}

/** Compact relative time ("just now", "5 min ago", "2 hours ago", "3 days ago"). */
export function relativeTime(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return "never"
  const min = Math.floor((now - new Date(iso).getTime()) / 60000)
  if (min < 1) return "just now"
  if (min < 60) return `${min} min ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`
  const d = Math.floor(hr / 24)
  return `${d} day${d === 1 ? "" : "s"} ago`
}
