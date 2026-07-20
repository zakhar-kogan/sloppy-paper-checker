import { describe, expect, it, vi } from "vitest";
import { exampleHref, exampleIdFromSearch, fetchExampleManifest, fetchExampleReport } from "./showcase";

describe("static example showcase", () => {
  it("creates refresh-safe query links without relying on SPA routes", () => {
    expect(exampleHref("attention-2017")).toBe("?example=attention-2017");
    expect(exampleIdFromSearch("?example=attention-2017")).toBe("attention-2017");
    expect(exampleIdFromSearch("?paper=10.1000/example")).toBeNull();
  });

  it("loads the manifest and report only from static example assets", async () => {
    const manifest = {
      schema_version: "1.0",
      generated_at: "2026-07-19T00:00:00Z",
      disclosure: "Examples only.",
      examples: [{
        id: "attention-2017",
        title: "Attention Is All You Need",
        year: 2017,
        identifier: "arXiv:1706.03762",
        profile: "computational_ml_modeling",
        content_level: "full_text",
        coverage: 0.75,
        review_score: 88,
        provisional: true,
        concern_count: 2,
        report: "reports/attention-2017.json",
        audit: "audits/attention-2017.json",
      }],
    } as const;
    const report = { schema_version: "1.3", identity: { title: "Attention Is All You Need" } };
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => manifest })
      .mockResolvedValueOnce({ ok: true, json: async () => report });

    const loadedManifest = await fetchExampleManifest(fetcher as unknown as typeof fetch);
    await fetchExampleReport(loadedManifest.examples[0], fetcher as unknown as typeof fetch);

    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const [url] of fetcher.mock.calls) {
      expect(String(url)).toContain("/examples/");
      expect(String(url)).not.toContain("/v1/");
    }
  });

  it("surfaces lazy-load failures", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: false });
    await expect(fetchExampleManifest(fetcher as unknown as typeof fetch))
      .rejects.toThrow("gallery could not be loaded");
  });
});
