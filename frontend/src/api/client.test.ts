import { describe, expect, it } from "vitest"

import { throwApiError } from "./client"

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("throwApiError", () => {
  it("surfaces the backend's human-readable detail string", async () => {
    const res = jsonResponse(
      { detail: "Select a drop-off location for this appraisal" },
      422,
    )
    await expect(throwApiError(res, "Appraisal failed")).rejects.toThrow(
      "Select a drop-off location for this appraisal",
    )
  })

  it("falls back to friendly text when detail is a validation array", async () => {
    // FastAPI request-validation errors put an array in `detail`, not a string.
    const res = jsonResponse({ detail: [{ loc: ["body"], msg: "bad" }] }, 422)
    await expect(throwApiError(res, "Save config failed")).rejects.toThrow(
      "Save config failed — please check your input and try again.",
    )
  })

  it("maps 401/403/5xx to friendly fallbacks", async () => {
    await expect(
      throwApiError(jsonResponse({}, 401), "x"),
    ).rejects.toThrow("Your session has expired. Please log in again.")
    await expect(
      throwApiError(jsonResponse({}, 403), "x"),
    ).rejects.toThrow("You don't have permission to do that.")
    await expect(
      throwApiError(jsonResponse({}, 503), "x"),
    ).rejects.toThrow("Something went wrong on our end. Please try again.")
  })

  it("falls back when the body is empty or not JSON", async () => {
    const res = new Response("not json", { status: 400 })
    await expect(throwApiError(res, "Add location failed")).rejects.toThrow(
      "Add location failed (error 400).",
    )
  })
})
