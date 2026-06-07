import type { Role } from "../api/types"

/** Manager-or-above: managers and the CEO may edit config and pricing rules.
 * Mirrors the backend's `require_role("manager")` gate (ADR-0005); the server
 * enforces it regardless — this is just for showing/hiding the right UI. */
export const isManager = (role: Role | undefined): boolean =>
  role === "manager" || role === "ceo"
