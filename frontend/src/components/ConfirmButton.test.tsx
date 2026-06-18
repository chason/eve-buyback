import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ConfirmButton } from "./ConfirmButton"

describe("ConfirmButton", () => {
  it("fires onConfirm only after a confirm step (#36)", async () => {
    const u = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <ConfirmButton
        label="Remove"
        confirmPrompt="Remove rule?"
        onConfirm={onConfirm}
      />,
    )

    // First click doesn't fire — it swaps to the prompt.
    await u.click(screen.getByRole("button", { name: "Remove" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByText("Remove rule?")).toBeInTheDocument()

    // Confirming fires it.
    await u.click(screen.getByRole("button", { name: "Yes" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it("cancelling returns to the trigger without firing", async () => {
    const u = userEvent.setup()
    const onConfirm = vi.fn()
    render(<ConfirmButton label="Revoke" onConfirm={onConfirm} />)

    await u.click(screen.getByRole("button", { name: "Revoke" }))
    await u.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onConfirm).not.toHaveBeenCalled()
    // Back to the trigger, no prompt lingering.
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Yes" })).not.toBeInTheDocument()
  })
})
