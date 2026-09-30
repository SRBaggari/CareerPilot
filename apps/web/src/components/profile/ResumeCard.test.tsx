import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Resume } from "@/lib/api/resumes";

import { ResumeCard } from "./ResumeCard";
import { stubApi } from "./testApi";

afterEach(() => vi.unstubAllGlobals());

const RESUME: Resume = {
  id: "r1",
  file_name: "priya.pdf",
  file_format: "pdf",
  file_size_bytes: 48_000,
  parse_status: "parsed",
  parse_error: null,
  parse_warnings: ["Couldn't read a work experience entry: 'Something vague'"],
  parser_name: "heuristic",
  is_primary: true,
  pending_suggestions: 12,
  total_suggestions: 12,
  created_at: "2026-01-01T00:00:00Z",
};

const fileInput = () => screen.getByLabelText(/choose resume file/i);

describe("ResumeCard", () => {
  it("uploads a PDF as multipart form data and refreshes", async () => {
    let uploaded = false;
    const calls = stubApi({
      "GET /resumes": () => ({ status: 200, body: uploaded ? [RESUME] : [] }),
      "POST /resumes": () => {
        uploaded = true;
        return { status: 201, body: { ...RESUME, parsed_text: "PRIYA" } };
      },
    });
    const onChanged = vi.fn(async () => {});
    render(<ResumeCard onChanged={onChanged} />);
    expect(await screen.findByText(/no resumes uploaded yet/i)).toBeInTheDocument();

    const file = new File(["%PDF-1.4"], "priya.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput(), { target: { files: [file] } });

    expect(await screen.findByText("priya.pdf")).toBeInTheDocument();
    expect(screen.getByText("12 to review")).toBeInTheDocument();
    expect(screen.getByText(/couldn't read a work experience entry/i)).toBeInTheDocument();
    const post = calls.find((c) => c.key === "POST /resumes");
    expect(post?.body).toBeInstanceOf(FormData);
    expect((post?.body as FormData).get("file")).toBe(file);
    expect(onChanged).toHaveBeenCalled();
  });

  it("rejects unsupported files before uploading", async () => {
    const calls = stubApi({});
    render(<ResumeCard onChanged={vi.fn(async () => {})} />);
    fireEvent.change(fileInput(), { target: { files: [new File(["hi"], "resume.txt")] } });
    expect(await screen.findByText("Upload a PDF or DOCX file.")).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /resumes")).toBe(false);
  });

  it("shows the server's explanation when a resume can't be read", async () => {
    stubApi({
      "GET /resumes": () => ({
        status: 200,
        body: [
          {
            ...RESUME,
            parse_status: "failed",
            parse_error: "No readable text was found.",
            parse_warnings: [],
            pending_suggestions: 0,
          },
        ],
      }),
    });
    render(<ResumeCard onChanged={vi.fn(async () => {})} />);
    expect(await screen.findByText("No readable text was found.")).toBeInTheDocument();
    expect(screen.getByText("Couldn't read")).toBeInTheDocument();
  });

  it("surfaces upload errors from the API", async () => {
    stubApi({
      "POST /resumes": () => ({
        status: 409,
        body: { detail: "You have already uploaded this file." },
      }),
    });
    render(<ResumeCard onChanged={vi.fn(async () => {})} />);
    fireEvent.change(fileInput(), { target: { files: [new File(["x"], "cv.docx")] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("already uploaded"));
  });
});
