import { publicConfig } from "@/lib/config";

type ValidationIssue = { loc?: (string | number)[]; msg?: string };

/** An error response from the API, with FastAPI validation errors mapped to fields. */
export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: Record<string, string>;
  readonly formErrors: string[];

  constructor(status: number, detail: unknown) {
    const fieldErrors: Record<string, string> = {};
    const formErrors: string[] = [];
    if (Array.isArray(detail)) {
      for (const issue of detail as ValidationIssue[]) {
        const message = (issue.msg ?? "Invalid value").replace(/^Value error, /, "");
        const loc = issue.loc ?? [];
        const field = loc[0] === "body" && typeof loc[1] === "string" ? loc[1] : null;
        if (field && !fieldErrors[field]) fieldErrors[field] = message;
        else if (!field) formErrors.push(message);
      }
    } else if (typeof detail === "string") {
      formErrors.push(detail);
    }
    const summary = formErrors[0] ?? Object.values(fieldErrors)[0] ?? `Request failed (${status})`;
    super(summary);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.formErrors = formErrors;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${publicConfig.apiBaseUrl}${path}`, {
      ...init,
      // FormData (file uploads) must set its own multipart boundary header.
      headers:
        init.body instanceof FormData
          ? init.headers
          : { "Content-Type": "application/json", ...init.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the CareerPilot API. Is the backend running?");
  }
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export function jsonBody(data: unknown): RequestInit["body"] {
  return JSON.stringify(data);
}
