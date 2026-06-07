import { describe, expect, it } from "vitest"

import { formatIsk } from "./format"

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
})
