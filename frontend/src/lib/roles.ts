import type { Role } from "../api/types"

/** Manager-or-above: managers and the CEO may edit config and pricing rules.
 * Mirrors the backend's `require_role("manager")` gate (ADR-0005); the server
 * enforces it regardless — this is just for showing/hiding the right UI. */
export const isManager = (role: Role | undefined): boolean =>
  role === "manager" || role === "ceo"

const ROLE_LABELS: Record<Role, string> = {
  member: "Member",
  manager: "Buyback Manager",
  ceo: "CEO",
}

/** A friendly, human-facing label for a role — never expose the raw enum value
 * to pilots. Falls back to the raw value for any future, unmapped role. */
export const roleLabel = (role: Role): string => ROLE_LABELS[role] ?? role
