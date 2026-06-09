import { apiGet } from "./client"

/** The app version (bumped per merged PR), shown in the footer. */
export const getVersion = () =>
  apiGet<{ version: string }>("/version")
