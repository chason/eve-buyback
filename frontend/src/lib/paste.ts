// Lightweight, client-side mirror of the backend paste parser's *line* handling
// (domain/paste.py): every non-blank line becomes exactly one appraisal line item —
// the in-game inventory copy is one stack per line, multibuy is one item per line.
// We only count here; the authoritative name→type resolution still happens server-side.

// EVE contracts hold at most 1000 distinct stacks, so an appraisal is capped at the
// same (backend: MAX_APPRAISAL_ITEMS in domain/paste.py). Surfaced inline so a member
// pasting a full hangar learns the limit before submitting, not via a 422.
export const MAX_APPRAISAL_ITEMS = 1000

/** How many line items a paste will contribute — non-blank lines, matching the
 * backend parser which skips blank lines and emits one item per remaining line. */
export function countPasteItems(text: string): number {
  let count = 0
  for (const raw of text.split(/\r?\n/)) {
    if (raw.trim()) count += 1
  }
  return count
}
