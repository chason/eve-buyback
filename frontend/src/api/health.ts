import { apiGet } from "./client"

export interface HealthResponse {
  status: string
  database: string
}

export const getHealth = () => apiGet<HealthResponse>("/health")
