import { describe, expect, it } from "vitest"

import { countPasteItems, MAX_APPRAISAL_ITEMS } from "./paste"

describe("countPasteItems", () => {
  it("counts non-blank lines, one item per line", () => {
    expect(countPasteItems("Tritanium\t1000\nPyerite 500")).toBe(2)
  })

  it("skips blank and whitespace-only lines, like the server parser", () => {
    expect(countPasteItems("Tritanium\n\n  \nPyerite\n")).toBe(2)
  })

  it("handles CRLF line endings (Windows / in-game copy)", () => {
    expect(countPasteItems("Tritanium\r\nPyerite\r\n")).toBe(2)
  })

  it("is zero for empty or whitespace-only input", () => {
    expect(countPasteItems("")).toBe(0)
    expect(countPasteItems("   \n\t\n")).toBe(0)
  })
})

describe("MAX_APPRAISAL_ITEMS", () => {
  it("mirrors EVE's 1000-stack contract cap (backend domain/paste.py)", () => {
    expect(MAX_APPRAISAL_ITEMS).toBe(1000)
  })
})
