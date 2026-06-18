// EVE Online runs on UTC, so the in-app clock (#114) is always UTC, never local time.
// Pure formatter (testable without a timer); the live ticking lives in the Layout hook.

/** Current EVE time formatted `HH:MM:SS` (24h, UTC) for the HUD footer clock. */
export function formatEveTime(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}
