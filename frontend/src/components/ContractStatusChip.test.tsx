import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { ContractStatusChip } from "./ContractStatusChip"

describe("ContractStatusChip", () => {
  it("labels each known status (ADR-0037)", () => {
    const cases: Record<string, string> = {
      in_progress: "In Progress",
      completed: "Completed",
      mismatch: "Mismatch",
      rejected: "Rejected",
      cancelled: "Cancelled",
      expired: "Expired",
      failed: "Failed",
    }
    for (const [status, label] of Object.entries(cases)) {
      const { unmount } = render(<ContractStatusChip status={status} />)
      expect(screen.getByText(label)).toBeInTheDocument()
      unmount()
    }
  })

  it("warns on a mismatch with an explanatory tooltip", () => {
    render(<ContractStatusChip status="mismatch" />)
    const chip = screen.getByText("Mismatch")
    // The tooltip lives on the wrapping span so a manager hovers to learn why.
    expect(chip.closest("span[title]")).toHaveAttribute(
      "title",
      expect.stringContaining("don't match"),
    )
  })

  it("renders an em dash for no / unknown contract", () => {
    const { rerender } = render(<ContractStatusChip status={null} />)
    expect(screen.getByText("—")).toBeInTheDocument()
    rerender(<ContractStatusChip status={undefined} />)
    expect(screen.getByText("—")).toBeInTheDocument()
    rerender(<ContractStatusChip status="bogus" />)
    expect(screen.getByText("—")).toBeInTheDocument()
  })
})
