import { apiGet } from "./client"

// The /health endpoint is a trivial liveness probe (dict[str, str]) with no DTO,
// so its shape is typed inline rather than generated.
export interface HealthResponse {
  status: string
  database: string
}

export const getHealth = () => apiGet<HealthResponse>("/health")
