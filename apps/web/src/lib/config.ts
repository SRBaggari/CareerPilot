/**
 * Public, browser-safe configuration. Only NEXT_PUBLIC_* variables may be read here —
 * secrets must never be referenced from code that can ship to the client.
 */
export const publicConfig = {
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
} as const;
