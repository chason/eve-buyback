import type { Role, SessionUser } from "../api/types"

/** Manager-or-above: managers and the CEO may edit config and pricing rules.
 * Mirrors the backend's `require_role("manager")` gate (ADR-0005); the server
 * enforces it regardless — this is just for showing/hiding the right UI. */
export const isManager = (role: Role | undefined): boolean =>
  role === "manager" || role === "ceo"

/** CEO or Director of a registered corp: may connect the Corp ESI access token and
 * designate Buyback Managers (ADR-0036). Mirrors the backend's
 * `require_ceo_or_director` gate; the server enforces it regardless. */
export const canManageCorp = (user: SessionUser | null | undefined): boolean =>
  !!user?.corporation_registered && (user.role === "ceo" || user.is_director)

const ROLE_LABELS: Record<Role, string> = {
  member: "Member",
  manager: "Buyback Manager",
  ceo: "CEO",
}

/** A friendly, human-facing label for a role — never expose the raw enum value
 * to pilots. Falls back to the raw value for any future, unmapped role. */
export const roleLabel = (role: Role): string => ROLE_LABELS[role] ?? role
