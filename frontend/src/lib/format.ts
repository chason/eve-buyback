/** Format a Decimal ISK string for display: thousands-grouped, 2 decimal places.
 *
 * The API sends money as Decimal *strings* (ADR-0020); we keep them as strings and
 * never run them through `Number()`, which would reintroduce float error. */
export function formatIsk(value: string): string {
  const [intPart, frac = ""] = value.split(".")
  const negative = intPart.startsWith("-")
  const digits = intPart.replace("-", "") || "0"
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
  const cents = frac.slice(0, 2).padEnd(2, "0")
  return `${negative ? "-" : ""}${grouped}.${cents} ISK`
}
