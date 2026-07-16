import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("thin extension manifest", () => {
  it("has only DOI handoff permissions and no embedded application UI", () => {
    const manifest = JSON.parse(readFileSync("public/manifest.json", "utf8"));
    expect(manifest.permissions).toEqual(["activeTab", "scripting"]);
    expect(manifest.side_panel).toBeUndefined();
    expect(manifest.options_page).toBeUndefined();
    expect(manifest.host_permissions).toBeUndefined();
  });
});
