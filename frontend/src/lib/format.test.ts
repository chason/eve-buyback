import { describe, expect, it } from "vitest"

import { formatIsk, formatIskCompact } from "./format"

describe("formatIsk", () => {
  it("groups thousands and shows two decimal places", () => {
    expect(formatIsk("4500.0000000000")).toBe("4,500.00 ISK")
    expect(formatIsk("4.50")).toBe("4.50 ISK")
    expect(formatIsk("0")).toBe("0.00 ISK")
    expect(formatIsk("1234567.5")).toBe("1,234,567.50 ISK")
  })

  it("keeps money as a string (no float rounding)", () => {
    // 9007199254740993 is beyond Number's safe integer range.
    expect(formatIsk("9007199254740993.00")).toBe("9,007,199,254,740,993.00 ISK")
  })

  it("handles scientific-notation Decimal strings (no '0E29.00 ISK')", () => {
    // Python's Decimal serializes extreme exponents this way (an unpriced mineral's value).
    expect(formatIsk("0E+29")).toBe("0.00 ISK")
    expect(formatIsk("0E-7")).toBe("0.00 ISK")
    expect(formatIsk("1E-8")).toBe("0.00 ISK")
    expect(formatIsk("1.5E+10")).toBe("15,000,000,000.00 ISK")
    expect(formatIsk("-2.5E+3")).toBe("-2,500.00 ISK")
    expect(formatIsk("1.2345E+2")).toBe("123.45 ISK")
    // No stray exponent marker survives to the output.
    expect(formatIsk("0E+29")).not.toContain("E")
  })
})

describe("formatIskCompact", () => {
  it("compacts to B / M / K with one decimal", () => {
    expect(formatIskCompact("4235000000.00")).toBe("4.2B")
    expect(formatIskCompact("850000000.00")).toBe("850M")
    expect(formatIskCompact("12500.00")).toBe("12.5K")
    expect(formatIskCompact("2100000000000")).toBe("2.1T")
  })

  it("drops the decimal when it's zero or the whole part fills three digits", () => {
    expect(formatIskCompact("4000000000")).toBe("4B")
    expect(formatIskCompact("999999999")).toBe("999M") // truncates, never rounds to 1B
    expect(formatIskCompact("123400000")).toBe("123M")
  })

  it("shows small and zero values plainly", () => {
    expect(formatIskCompact("850.75")).toBe("850")
    expect(formatIskCompact("0")).toBe("0")
    expect(formatIskCompact("0E+29")).toBe("0")
  })

  it("keeps the sign and survives unsafe-integer magnitudes", () => {
    expect(formatIskCompact("-2500000000")).toBe("-2.5B")
    expect(formatIskCompact("9007199254740993")).toBe("9007T")
  })
})
