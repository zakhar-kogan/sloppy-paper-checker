import { describe, expect, it } from "vitest";
import { duration, isResolvableInput } from "./intake";

describe("single-action paper intake", () => {
  it("starts automatic preflight only for complete identifiers and URLs", () => {
    expect(isResolvableInput("10.1016/S0140-6736(17)32802-7")).toBe(true);
    expect(isResolvableInput("https://pubmed.ncbi.nlm.nih.gov/41366844/")).toBe(true);
    expect(isResolvableInput("PMC12910469")).toBe(true);
    expect(isResolvableInput("10.1016/")).toBe(false);
  });

  it("formats live stage duration without false precision", () => {
    expect(duration(7)).toBe("7s");
    expect(duration(72)).toBe("1m 12s");
  });
});
