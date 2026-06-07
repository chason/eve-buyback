// Registers jest-dom matchers (e.g. toBeInTheDocument) on Vitest's `expect`.
import "@testing-library/jest-dom/vitest"

import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

// Vitest runs without `globals`, so Testing Library's automatic afterEach
// cleanup never registers — unmount between tests ourselves so rendered DOMs
// don't accumulate across `it`s in the same file.
afterEach(cleanup)
