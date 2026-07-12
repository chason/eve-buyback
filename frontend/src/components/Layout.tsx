import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link, NavLink, Outlet } from "react-router-dom"

import { getMe, logout } from "../api/auth"
import { getVersion } from "../api/version"
import { formatEveTime } from "../lib/eveTime"
import { canManageCorp, isManager } from "../lib/roles"

// Console-tab treatment for the active route (#87): NavLink toggles `active`, styled
// in index.css. `end` on Appraisals so it doesn't stay lit under detail routes.
const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? "nav-link active" : "nav-link"

// Live EVE time (#114) — EVE runs on UTC. One interval, ticking the HUD-footer clock
// once a second; cleaned up on unmount.
function useEveClock(): string {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return formatEveTime(now)
}

export default function Layout() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const version = useQuery({
    queryKey: ["version"],
    queryFn: getVersion,
    staleTime: Infinity,
  })
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  })
  const eveTime = useEveClock()
  const user = me.data
  const showManagerLinks = user?.corporation_registered && isManager(user.role)
  // CEO/Director can designate managers even if they aren't managers themselves (ADR-0036).
  const showManagersLink = canManageCorp(user)

  return (
    <>
      <nav className="container">
        <ul>
          <li>
            <Link to="/" className="brand">
              <img
                src="/favicon.svg"
                alt=""
                className="brand-logo"
                width="22"
                height="22"
              />
              <span className="brand-name">BUYBACK</span>
              <span className="brand-codename">Corp&nbsp;Logistics</span>
            </Link>
          </li>
        </ul>
        {user && (
          <ul>
            <li>
              <NavLink to="/appraise" className={navClass}>
                Appraise
              </NavLink>
            </li>
            <li>
              <NavLink to="/appraisals" end className={navClass}>
                Appraisals
              </NavLink>
            </li>
            {showManagerLinks && (
              <>
                <li>
                  <NavLink to="/stock" className={navClass}>
                    Stock
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/config" className={navClass}>
                    Config
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/rules" className={navClass}>
                    Rules
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/locations" className={navClass}>
                    Locations
                  </NavLink>
                </li>
              </>
            )}
            {showManagersLink && (
              <li>
                <NavLink to="/managers" className={navClass}>
                  Managers
                </NavLink>
              </li>
            )}
            {user.is_app_admin && (
              <li>
                <NavLink to="/admin" className={navClass}>
                  Admin
                </NavLink>
              </li>
            )}
            <li>
              <button
                type="button"
                className="nav-link"
                onClick={() => logoutMutation.mutate()}
              >
                Log out
              </button>
            </li>
          </ul>
        )}
      </nav>
      <main className="container">
        <Outlet />
      </main>
      <footer className="hud-status">
        <span className="hud-dot" aria-hidden="true" />
        <span>Buyback{version.data ? ` v${version.data.version}` : ""}</span>
        <span className="hud-node">· Node: {window.location.hostname}</span>
        <Link className="hud-link" to="/privacy">
          · Privacy
        </Link>
        <a
          className="hud-link"
          href="https://github.com/chason/eve-buyback"
          target="_blank"
          rel="noreferrer"
        >
          · Source
        </a>
        <span className="hud-session">
          {user && <span className="hud-user">{user.character_name}</span>}
          <span className="hud-clock" title="Current EVE time">
            EVE Time {eveTime}
          </span>
        </span>
      </footer>
    </>
  )
}
