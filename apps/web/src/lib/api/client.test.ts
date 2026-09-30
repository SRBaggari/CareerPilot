import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());

const respond = (status: number, body?: unknown) =>
  vi
    .fn()
    .mockResolvedValue(new Response(body === undefined ? null : JSON.stringify(body), { status }));

describe("ApiError", () => {
  it("maps FastAPI validation errors onto fields", () => {
    const error = new ApiError(422, [
      { loc: ["body", "end_date"], msg: "Value error, end_date must not be before the start date" },
      { loc: ["body", "work_modes", 0], msg: "Input should be 'onsite', 'hybrid' or 'remote'" },
      { loc: ["body"], msg: "Value error, a current position cannot have an end_date" },
    ]);
    expect(error.fieldErrors).toEqual({
      end_date: "end_date must not be before the start date",
      work_modes: "Input should be 'onsite', 'hybrid' or 'remote'",
    });
    expect(error.formErrors).toEqual(["a current position cannot have an end_date"]);
    expect(error.message).toBe("a current position cannot have an end_date");
  });

  it("uses a string detail as the message", () => {
    const error = new ApiError(409, "A profile already exists for this user.");
    expect(error.message).toBe("A profile already exists for this user.");
    expect(error.fieldErrors).toEqual({});
  });
});

describe("apiFetch", () => {
  it("returns parsed JSON on success", async () => {
    vi.stubGlobal("fetch", respond(200, { ok: true }));
    await expect(apiFetch("/x")).resolves.toEqual({ ok: true });
  });

  it("returns undefined for 204 No Content", async () => {
    vi.stubGlobal("fetch", respond(204));
    await expect(apiFetch("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("throws ApiError with the status on failure", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: "Profile not found." }));
    await expect(apiFetch("/x")).rejects.toMatchObject({
      status: 404,
      message: "Profile not found.",
    });
  });

  it("reports an unreachable backend clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(apiFetch("/x")).rejects.toMatchObject({ status: 0 });
  });
});
