import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { createAppraisal } from "../api/appraisals"
import { searchTypes } from "../api/sde"

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

  const query = search.trim()
  const results = useQuery({
    queryKey: ["types", query],
    queryFn: () => searchTypes(query),
    enabled: query.length >= 2,
  })

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

  const canSubmit = items.length > 0 || paste.trim().length > 0

  function submit() {
    appraise.mutate({
      items: items.map((i) => ({ type_id: i.type_id, quantity: i.quantity })),
      paste: paste.trim() ? paste : null,
    })
  }

  return (
    <>
      <h1>Appraise</h1>

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
                    value={i.quantity}
                    aria-label={`Quantity for ${i.name}`}
                    onChange={(e) =>
                      setQuantity(i.type_id, Math.max(1, Number(e.target.value) || 1))
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
        Appraise
      </button>
      {appraise.isError && (
        <p className="error">{(appraise.error as Error).message}</p>
      )}
    </>
  )
}
