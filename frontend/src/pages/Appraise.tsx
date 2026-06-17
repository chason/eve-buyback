import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { createAppraisal } from "../api/appraisals"
import { listLocations } from "../api/locations"
import { getConfig } from "../api/pricing"
import { searchTypes } from "../api/sde"
import { hubName } from "../lib/hubs"

interface PickedItem {
  type_id: number
  name: string
  quantity: number
}

export default function Appraise() {
  const navigate = useNavigate()
  const [paste, setPaste] = useState("")
  const [items, setItems] = useState<PickedItem[]>([])
  const [search, setSearch] = useState("")
  const [location, setLocation] = useState("")

  const query = search.trim()
  const results = useQuery({
    queryKey: ["types", query],
    queryFn: () => searchTypes(query),
    enabled: query.length >= 2,
  })

  // Drop-off locations (ADR-0030): if the corp configured any, picking one is
  // required; otherwise the appraisal falls back to the market hub (shown for clarity).
  const locations = useQuery({ queryKey: ["locations"], queryFn: listLocations })
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig })
  const hasLocations = (locations.data?.length ?? 0) > 0

  const appraise = useMutation({
    mutationFn: createAppraisal,
    onSuccess: (data) => navigate(`/a/${data.public_id}`),
  })

  function addItem(typeId: number, name: string) {
    setItems((prev) => {
      const existing = prev.find((i) => i.type_id === typeId)
      if (existing) {
        return prev.map((i) =>
          i.type_id === typeId ? { ...i, quantity: i.quantity + 1 } : i,
        )
      }
      return [...prev, { type_id: typeId, name, quantity: 1 }]
    })
    setSearch("")
  }

  function setQuantity(typeId: number, quantity: number) {
    setItems((prev) =>
      prev.map((i) => (i.type_id === typeId ? { ...i, quantity } : i)),
    )
  }

  function removeItem(typeId: number) {
    setItems((prev) => prev.filter((i) => i.type_id !== typeId))
  }

  const hasItems = items.length > 0 || paste.trim().length > 0
  const canSubmit = hasItems && (!hasLocations || location !== "")

  function submit() {
    appraise.mutate({
      items: items.map((i) => ({ type_id: i.type_id, quantity: i.quantity })),
      paste: paste.trim() ? paste : null,
      delivery_location_id: hasLocations ? location : null,
    })
  }

  return (
    <>
      <h1>Appraise</h1>

      {hasLocations ? (
        <label>
          Drop-off location
          <select
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            aria-label="Drop-off location"
            required
          >
            <option value="">Select where you'll deliver…</option>
            {locations.data?.map((loc) => (
              <option key={loc.location_id} value={loc.location_id}>
                {loc.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        config.data && (
          <p>
            <small className="field-hint">
              Drop-off:{" "}
              <strong>
                {config.data.market_hub_name ?? hubName(config.data.market_hub_id)}
              </strong>{" "}
              (corp default)
            </small>
          </p>
        )
      )}

      <label>
        Paste items
        <textarea
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder={"Tritanium\t1000\nPyerite 500"}
          rows={6}
        />
      </label>

      <label>
        Add an item
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name…"
          aria-label="Search by name"
        />
      </label>
      {query.length >= 2 && (
        <ul className="search-results">
          {results.isLoading && <li aria-busy="true">Searching…</li>}
          {results.data?.map((t) => (
            <li key={t.type_id}>
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault()
                  addItem(t.type_id, t.name)
                }}
              >
                {t.name}
              </a>
            </li>
          ))}
          {results.data?.length === 0 && <li>No matches.</li>}
        </ul>
      )}

      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Item</th>
              <th>Qty</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.type_id}>
                <td>{i.name}</td>
                <td>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={i.quantity}
                    aria-label={`Quantity for ${i.name}`}
                    onChange={(e) =>
                      setQuantity(
                        i.type_id,
                        Math.max(1, Math.trunc(Number(e.target.value)) || 1),
                      )
                    }
                  />
                </td>
                <td>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      removeItem(i.type_id)
                    }}
                  >
                    Remove
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <button onClick={submit} disabled={!canSubmit} aria-busy={appraise.isPending}>
        Create appraisal
      </button>
      <small className="submit-note">
        This creates a saved appraisal record your corp&apos;s Buyback Managers can see
        in History — it&apos;s your priced quote, not yet an in-game contract.
      </small>
      {appraise.isError && (
        <p className="error">{(appraise.error as Error).message}</p>
      )}
    </>
  )
}
