import { useState, type ReactNode } from "react"

// A two-step confirm for a destructive action (#36): the trigger swaps in place to a
// "<prompt> Yes / Cancel" line instead of firing the mutation on the first click, so
// revoke/remove can't go off by accident. Cancel is the default; Yes runs `onConfirm`.
export function ConfirmButton({
  onConfirm,
  label,
  confirmPrompt = "Are you sure?",
  confirmLabel = "Yes",
  className,
  disabled,
}: {
  onConfirm: () => void
  label: ReactNode
  confirmPrompt?: ReactNode
  confirmLabel?: string
  className?: string
  disabled?: boolean
}) {
  const [confirming, setConfirming] = useState(false)

  if (!confirming) {
    return (
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => setConfirming(true)}
      >
        {label}
      </button>
    )
  }

  return (
    <span className="confirm-inline" role="status">
      {confirmPrompt}{" "}
      <button
        type="button"
        className="linkbtn confirm-yes"
        onClick={() => {
          setConfirming(false)
          onConfirm()
        }}
      >
        {confirmLabel}
      </button>{" "}
      <button
        type="button"
        className="linkbtn"
        onClick={() => setConfirming(false)}
      >
        Cancel
      </button>
    </span>
  )
}
