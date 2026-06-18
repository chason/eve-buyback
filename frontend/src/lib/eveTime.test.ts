import { describe, expect, it } from "vitest"

import { formatEveTime } from "./eveTime"

describe("formatEveTime", () => {
  it("formats UTC time as zero-padded HH:MM:SS", () => {
    expect(formatEveTime(new Date("2026-06-18T14:32:07Z"))).toBe("14:32:07")
  })

  it("uses UTC getters, never local time", () => {
    // getUTC* ignores the host timezone — the EVE clock is always UTC (#114).
    expect(formatEveTime(new Date("2026-06-18T23:05:09Z"))).toBe("23:05:09")
  })

  it("pads single digits and midnight", () => {
    expect(formatEveTime(new Date("2026-01-02T00:00:00Z"))).toBe("00:00:00")
  })
})
