import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router-dom"

import { getMe } from "../api/auth"
import { addLocation, listLocations, removeLocation } from "../api/locations"
import { searchStations } from "../api/sde"
import { getStructureStatus, searchStructures } from "../api/structures"
import { StatusChip } from "../components/StatusChip"
import { isManager } from "../lib/roles"

export default function Locations() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const canEdit = isManager(me.data?.role)

  const locations = useQuery({ queryKey: ["locations"], queryFn: listLocations })

  const [stationQuery, setStationQuery] = useState("")
  const [structureQuery, setStructureQuery] = useState("")

  const sQuery = stationQuery.trim()
  const stationResults = useQuery({
    queryKey: ["stations", sQuery],
    queryFn: () => searchStations(sQuery),
    enabled: canEdit && sQuery.length >= 2,
  })

  const structureStatus = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: canEdit,
  })
  const structuresAvailable = structureStatus.data?.configured !== false
  const authorized = !!structureStatus.data?.authorized
  const stQuery = structureQuery.trim()
  const structureResults = useQuery({
    queryKey: ["structures", stQuery],
    queryFn: () => searchStructures(stQuery),
    enabled: canEdit && authorized && stQuery.length >= 3,
  })

  const add = useMutation({
    mutationFn: addLocation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["locations"] })
      setStationQuery("")
      setStructureQuery("")
    },
  })
  const remove = useMutation({
    mutationFn: removeLocation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["locations"] }),
  })

  if (locations.isLoading) return <p aria-busy="true">Loading…</p>
  if (locations.isError || !locations.data) {
    return <p className="error">Could not load drop-off locations.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Drop-off locations</h1>
        <p>
          Where members deliver bought-back items. Members pick one when getting an
          appraisal. This is separate from the market hub used for pricing.
        </p>
      </hgroup>

      {locations.data.length === 0 ? (
        <p>
          No drop-off locations yet.{" "}
          {canEdit
            ? "Add one below — until then, appraisals use the market hub."
            : "Appraisals use the market hub until a manager adds one."}
        </p>
      ) : (
        <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Location</th>
              <th>Type</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {locations.data.map((loc) => (
              <tr key={loc.location_id}>
                <td>{loc.name}</td>
                <td>
                  {loc.kind === "structure" ? (
                    <StatusChip variant="info">Structure</StatusChip>
                  ) : (
                    <StatusChip variant="muted">NPC station</StatusChip>
                  )}
                </td>
                {canEdit && (
                  <td>
                    <button
                      type="button"
                      className="linkbtn"
                      onClick={() => remove.mutate(loc.location_id)}
                    >
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {!canEdit && (
        <p>
          <small>Only a Buyback Manager can change these.</small>
        </p>
      )}

      {canEdit && (
        <>
          <h2>Add a location</h2>

          <div className="panel">
          <label>
            NPC station
            <input
              type="search"
              value={stationQuery}
              placeholder="Search by system or station…"
              aria-label="Search station"
              onChange={(e) => setStationQuery(e.target.value)}
            />
            {sQuery.length >= 2 && (
              <ul className="search-results">
                {stationResults.isLoading && (
                  <li aria-busy="true">Searching…</li>
                )}
                {stationResults.data?.map((s) => (
                  <li key={s.station_id}>
                    <button
                      type="button"
                      onClick={() =>
                        add.mutate({
                          location_id: String(s.station_id),
                          kind: "npc_station",
                        })
                      }
                    >
                      {s.system_name} - {s.name}
                    </button>
                  </li>
                ))}
                {stationResults.data?.length === 0 && <li>No matches.</li>}
              </ul>
            )}
          </label>

          <label>
            Player structure
            {!structuresAvailable ? (
              <small className="field-hint">
                Not available on this server — the operator hasn&apos;t configured
                the token-encryption key (BUYBACK_TOKEN_ENCRYPTION_KEY).
              </small>
            ) : authorized ? (
              <>
                <input
                  type="search"
                  value={structureQuery}
                  placeholder="Search structures by name…"
                  aria-label="Search structure"
                  onChange={(e) => setStructureQuery(e.target.value)}
                />
                {stQuery.length >= 3 && (
                  <ul className="search-results">
                    {structureResults.isLoading && (
                      <li aria-busy="true">Searching…</li>
                    )}
                    {structureResults.isError && (
                      <li className="error">
                        Search failed — your structure access may have expired.
                      </li>
                    )}
                    {structureResults.data?.map((s) => (
                      <li key={s.structure_id}>
                        <button
                          type="button"
                          onClick={() =>
                            add.mutate({
                              location_id: s.structure_id,
                              kind: "structure",
                              name: s.name,
                            })
                          }
                        >
                          {s.name}
                        </button>
                      </li>
                    ))}
                    {structureResults.data?.length === 0 && <li>No matches.</li>}
                  </ul>
                )}
                <small className="field-hint">
                  Only structures your authorized character can dock at appear.
                </small>
              </>
            ) : (
              <small className="field-hint">
                To add a player structure, authorize structure access on the{" "}
                <Link to="/config">Config page</Link> first.
              </small>
            )}
          </label>

          {add.isError && (
            <p className="error">{(add.error as Error).message}</p>
          )}
          </div>
        </>
      )}
    </>
  )
}
