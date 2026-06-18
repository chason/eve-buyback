import { useEffect, useRef, useState, type ReactNode } from "react"

// A confirmation popup for a destructive action (#36): clicking the trigger opens a modal
// (Pico `<dialog>`) asking to confirm, so revoke/remove can't fire by accident. Cancel is
// the default focus; Esc or a backdrop click also cancels. Only "Yes" runs `onConfirm`.
export function ConfirmButton({
  onConfirm,
  label,
  title = "Please confirm",
  prompt,
  confirmLabel,
  className,
  disabled,
}: {
  onConfirm: () => void
  label: ReactNode
  title?: string
  prompt: ReactNode
  confirmLabel: string
  className?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    cancelRef.current?.focus() // land on the safe option
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open])

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
      {open && (
        <dialog
          open
          className="confirm-dialog"
          aria-label={title}
          // A click on the backdrop (the dialog itself, not the article) cancels.
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false)
          }}
        >
          <article>
            <header>
              <strong>{title}</strong>
            </header>
            <p>{prompt}</p>
            <footer>
              <button
                type="button"
                className="secondary"
                ref={cancelRef}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="confirm-yes"
                onClick={() => {
                  setOpen(false)
                  onConfirm()
                }}
              >
                {confirmLabel}
              </button>
            </footer>
          </article>
        </dialog>
      )}
    </>
  )
}
