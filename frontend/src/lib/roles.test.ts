import { describe, expect, it } from "vitest"

import { isManager, roleLabel } from "./roles"

describe("isManager", () => {
  it("is true for managers and the CEO, false otherwise", () => {
    expect(isManager("manager")).toBe(true)
    expect(isManager("ceo")).toBe(true)
    expect(isManager("member")).toBe(false)
    expect(isManager(undefined)).toBe(false)
  })
})

describe("roleLabel", () => {
  it("maps raw enum values to friendly labels", () => {
    expect(roleLabel("member")).toBe("Member")
    expect(roleLabel("manager")).toBe("Buyback Manager")
    expect(roleLabel("ceo")).toBe("CEO")
  })
})
