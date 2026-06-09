import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, Outlet } from "react-router-dom"

import { getMe, logout } from "../api/auth"
import { getVersion } from "../api/version"
import { isManager } from "../lib/roles"

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
  const user = me.data
  const showManagerLinks = user?.corporation_registered && isManager(user.role)

  return (
    <>
      <nav className="container">
        <ul>
          <li>
            <Link to="/" className="contrast">
              <strong>Buyback</strong>
            </Link>
            {version.data && (
              <small
                style={{
                  marginLeft: "0.4rem",
                  fontSize: "0.7em",
                  color: "var(--pico-muted-color)",
                }}
              >
                v{version.data.version}
              </small>
            )}
          </li>
        </ul>
        {user && (
          <ul>
            <li>
              <Link to="/appraise">Appraise</Link>
            </li>
            <li>
              <Link to="/appraisals">Appraisals</Link>
            </li>
            {showManagerLinks && (
              <>
                <li>
                  <Link to="/config">Config</Link>
                </li>
                <li>
                  <Link to="/rules">Rules</Link>
                </li>
              </>
            )}
            <li>{user.character_name}</li>
            <li>
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault()
                  logoutMutation.mutate()
                }}
              >
                Log out
              </a>
            </li>
          </ul>
        )}
      </nav>
      <main className="container">
        <Outlet />
      </main>
    </>
  )
}
