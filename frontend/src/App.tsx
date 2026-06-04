import { useQuery } from "@tanstack/react-query"

import { getHealth } from "./api/health"

export default function App() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  })

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>Buyback</h1>
      <p>EVE Online corporation buyback — scaffold.</p>
      <section>
        <h2>Backend health</h2>
        {isLoading && <p>Checking…</p>}
        {isError && <p style={{ color: "crimson" }}>Error: {(error as Error).message}</p>}
        {data && (
          <pre style={{ background: "#f4f4f4", padding: "0.75rem", borderRadius: 6 }}>
            status: {data.status}
            {"\n"}database: {data.database}
          </pre>
        )}
      </section>
    </main>
  )
}
