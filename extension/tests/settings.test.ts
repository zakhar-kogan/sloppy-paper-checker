import {describe, expect, it} from "vitest";

describe("endpoint policy", () => {
  it("rejects executable schemes", () => {
    const allowed = (value: string) => ["http:", "https:"].includes(new URL(value).protocol);
    expect(allowed("javascript:alert(1)")).toBe(false);
    expect(allowed("http://127.0.0.1:8787")).toBe(true);
  });
});

