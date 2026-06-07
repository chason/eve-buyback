import { useQuery } from "@tanstack/react-query"
import { Navigate, Outlet } from "react-router-dom"

import { getMe } from "../api/auth"

/** Gate child routes behind a session; bounce to the home/login page otherwise. */
export default function RequireAuth() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  if (me.isLoading) return <p aria-busy="true">Loading…</p>
  if (!me.data) return <Navigate to="/" replace />
  return <Outlet />
}
