import { describe, expect, it } from "vitest";

import { emptyValues, toPayload, validate, valuesFromItem, type FieldDef } from "./form";

const FIELDS: FieldDef[] = [
  { name: "title", label: "Title", type: "text", required: true, maxLength: 5 },
  { name: "url", label: "URL", type: "url" },
  { name: "start_date", label: "Start", type: "date" },
  { name: "is_current", label: "Current", type: "checkbox" },
];

describe("profile form helpers", () => {
  it("round-trips API items through form values", () => {
    const values = valuesFromItem(FIELDS, { title: "Proj", url: null, is_current: true });
    expect(values).toEqual({ title: "Proj", url: "", start_date: "", is_current: true });
    expect(toPayload(FIELDS, values)).toEqual({
      title: "Proj",
      url: null,
      start_date: null,
      is_current: true,
    });
  });

  it("trims text and turns blanks into null so optional fields are cleared", () => {
    expect(
      toPayload(FIELDS, { ...emptyValues(FIELDS), title: "  Hi  ", url: "   " }),
    ).toMatchObject({
      title: "Hi",
      url: null,
    });
  });

  it("validates required and max-length fields", () => {
    expect(validate(FIELDS, { ...emptyValues(FIELDS), title: "  " })).toEqual({
      title: "Title is required",
    });
    expect(validate(FIELDS, { ...emptyValues(FIELDS), title: "Too long" })).toEqual({
      title: "Title must be at most 5 characters",
    });
    expect(validate(FIELDS, { ...emptyValues(FIELDS), title: "OK" })).toEqual({});
  });
});
