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

/** Format a Decimal ISK string compactly for dashboards: "4.2B", "850M", "12.5K",
 * whole numbers below a thousand ("850"). One decimal, dropped when it's 0. Pure
 * string surgery on the integer digits — no `Number()`, so no float error and no
 * precision cliff on huge treasuries. Truncates rather than rounds (999,999,999 is
 * "999M", not a misleading "1B"); the decimal is dropped once the whole part has
 * three digits. */
export function formatIskCompact(value: string): string {
  const [intPart] = expandScientific(value).split(".")
  const negative = intPart.startsWith("-")
  const digits = intPart.replace("-", "").replace(/^0+(?=\d)/, "") || "0"
  const units: Array<[string, number]> = [
    ["T", 13],
    ["B", 10],
    ["M", 7],
    ["K", 4],
  ]
  for (const [suffix, minDigits] of units) {
    if (digits.length >= minDigits) {
      const whole = digits.slice(0, digits.length - minDigits + 1)
      const tenth = digits[digits.length - minDigits + 1]
      const frac = tenth && tenth !== "0" && whole.length < 3 ? `.${tenth}` : ""
      return `${negative ? "-" : ""}${whole}${frac}${suffix}`
    }
  }
  return `${negative ? "-" : ""}${digits}`
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
