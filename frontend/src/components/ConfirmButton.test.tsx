import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ConfirmButton } from "./ConfirmButton"

describe("ConfirmButton", () => {
  it("opens a popup and fires onConfirm only after confirming (#36)", async () => {
    const u = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <ConfirmButton
        label="Remove"
        title="Remove rule?"
        prompt="This pricing rule will be deleted."
        confirmLabel="Remove rule"
        onConfirm={onConfirm}
      />,
    )

    // No dialog until the trigger is clicked.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await u.click(screen.getByRole("button", { name: "Remove" }))
    const dialog = screen.getByRole("dialog")
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText("This pricing rule will be deleted.")).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()

    // The distinct confirm button (not the trigger) fires it and closes the popup.
    await u.click(screen.getByRole("button", { name: "Remove rule" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("Cancel and Escape close the popup without firing", async () => {
    const u = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <ConfirmButton
        label="Revoke"
        prompt="This stops pricing."
        confirmLabel="Revoke"
        onConfirm={onConfirm}
      />,
    )

    await u.click(screen.getByRole("button", { name: "Revoke" }))
    await u.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    // Re-open, then Escape cancels.
    await u.click(screen.getByRole("button", { name: "Revoke" }))
    await u.keyboard("{Escape}")
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
