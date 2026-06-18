/** Expand a scientific-notation decimal string (e.g. "0E+29", "1.5E+10", "1E-8") to plain
 *  fixed-point, by string surgery — no `Number()`, so no float error. Returns the input
 *  unchanged when it isn't in exponent form. Python's Decimal serializes extreme exponents
 *  this way (an unpriced mineral's `0E+29`); the backend normalizes them, but guard here so
 *  a stray scientific string never renders as "0E29.00 ISK". */
function expandScientific(value: string): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$/.exec(value)
  if (!m) return value
  const [, sign, intPart, fracPart = "", expStr] = m
  const digits = intPart + fracPart
  // The point sits after intPart.length digits, then shifts right by the exponent.
  const point = intPart.length + Number(expStr)
  let out: string
  if (point <= 0) {
    out = "0." + "0".repeat(-point) + digits
  } else if (point >= digits.length) {
    out = digits + "0".repeat(point - digits.length)
  } else {
    out = `${digits.slice(0, point)}.${digits.slice(point)}`
  }
  return sign + out
}

/** Format a Decimal ISK string for display: thousands-grouped, 2 decimal places.
 *
 * The API sends money as Decimal *strings* (ADR-0020); we keep them as strings and
 * never run them through `Number()`, which would reintroduce float error. */
export function formatIsk(value: string): string {
  const [intPart, frac = ""] = expandScientific(value).split(".")
  const negative = intPart.startsWith("-")
  // Strip leading zeros (keep one) so an expanded "000…0" doesn't render as "0,000,…".
  const digits = intPart.replace("-", "").replace(/^0+(?=\d)/, "") || "0"
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
  const cents = frac.slice(0, 2).padEnd(2, "0")
  return `${negative ? "-" : ""}${grouped}.${cents} ISK`
}
