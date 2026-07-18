import { describe, expect, it } from "vitest";
import type { AnalysisReport } from "./domain";
import { buildAssessmentGroups, coverageStateLabel, findingDisplayTitle, moduleStateLabel } from "./report";

type Finding = AnalysisReport["findings"][number];
type Grade = Finding["grade"];

const finding = (category: string, rubricItem: string, grade: Grade, title?: string): Finding => ({
  id: `${category}-${rubricItem}`,
  category,
  rubric_item: rubricItem,
  title: title ?? rubricItem.replaceAll("_", " "),
  explanation: "Grounded explanation.",
  severity: grade === "critical_concern" ? "critical" : grade === "major_concern" ? "major" : grade === "minor_concern" ? "minor" : "info",
  grade,
  confidence: grade === "not_assessed" ? 0 : 0.9,
  paper_spans: [],
  external_sources: [],
  affected_conclusions: [],
  counterevidence: [],
  limitations: [],
  critic_disposition: "accepted",
});

const report = (overrides: Partial<AnalysisReport>): AnalysisReport => ({
  findings: [],
  dimensions: [],
  module_statuses: [],
  ...overrides,
} as AnalysisReport);

describe("grouped report assessment", () => {
  it("preserves category order and sorts items by severity without scrambling ties", () => {
    const result = buildAssessmentGroups(report({
      module_statuses: [
        { key: "claims", label: "Claims", state: "completed", assessed_items: 2, expected_items: 2, limitation: null },
        { key: "design", label: "Design", state: "completed", assessed_items: 4, expected_items: 5, limitation: null },
      ],
      dimensions: [
        { key: "design", label: "Design", weight: 30, score: 61.2, assessed_items: 4, total_items: 5 },
        { key: "claims", label: "Claims", weight: 20, score: 100, assessed_items: 2, total_items: 2 },
      ],
      findings: [
        finding("design", "clean_item", "no_concern"),
        finding("claims", "first_clean_item", "no_concern"),
        finding("design", "minor_item", "minor_concern"),
        finding("design", "critical_item", "critical_concern"),
        finding("design", "gap_item", "not_assessed"),
        finding("claims", "second_clean_item", "no_concern"),
        finding("design", "major_item", "major_concern"),
      ],
    }));

    expect(result.map((group) => group.key)).toEqual(["claims", "design"]);
    expect(result[0].items.map((item) => item.rubric_item)).toEqual(["first_clean_item", "second_clean_item"]);
    expect(result[1].items.map((item) => item.grade)).toEqual([
      "critical_concern",
      "major_concern",
      "minor_concern",
      "not_assessed",
      "no_concern",
    ]);
    expect(result[1]).toMatchObject({ concernCount: 3, concernLabel: "3 concerns", hasConcern: true, gapCount: 1, score: 61.2 });
    expect(result[0]).toMatchObject({ concernCount: 0, concernLabel: "No concerns", hasConcern: false, gapCount: 0, score: 100 });
  });

  it("keeps gap-only categories collapsed and suppresses misleading zero scores", () => {
    const [group] = buildAssessmentGroups(report({
      module_statuses: [
        { key: "design", label: "Design", state: "module_failed", assessed_items: 0, expected_items: 2, limitation: "Provider failed." },
      ],
      dimensions: [
        { key: "design", label: "Design", weight: 30, score: 0, assessed_items: 0, total_items: 2 },
      ],
      findings: [finding("design", "first_item", "not_assessed"), finding("design", "second_item", "not_assessed")],
    }));

    expect(group).toMatchObject({ hasConcern: false, concernCount: 0, concernLabel: "No items assessed", gapCount: 2, score: null });
    expect(moduleStateLabel(group.state)).toBe("Review failed");
  });

  it("strips only recognized legacy grade suffixes from titles", () => {
    expect(findingDisplayTitle(finding("design", "protocol", "no_concern", "Protocol: no concern"))).toBe("Protocol");
    expect(findingDisplayTitle(finding("design", "protocol", "minor_concern", "Protocol: CRITICAL CONCERN"))).toBe("Protocol");
    expect(findingDisplayTitle(finding("design", "protocol", "no_concern", "Protocol: preregistered"))).toBe("Protocol: preregistered");
    expect(findingDisplayTitle(finding("design", "risk_of_bias", "not_assessed", "Risk Of Bias: not assessed"))).toBe("Risk Of Bias");
  });

  it("labels every process state independently from assessment coverage", () => {
    expect(moduleStateLabel("completed")).toBe("Review completed");
    expect(moduleStateLabel("ineligible_at_content_level")).toBe("Unavailable for this content");
    expect(moduleStateLabel("unreviewed")).toBe("Review incomplete");
    expect(moduleStateLabel(null)).toBe("Review status unavailable");
    expect(coverageStateLabel(false)).toBe("Final coverage");
    expect(coverageStateLabel(true)).toBe("Provisional coverage");
  });

  it("keeps legacy findings visible when module statuses are absent", () => {
    const [group] = buildAssessmentGroups(report({
      dimensions: [{ key: "claims", label: "Claim alignment", weight: 20, score: 75, assessed_items: 1, total_items: 1 }],
      findings: [finding("claims", "claim_strength", "minor_concern")],
    }));

    expect(group).toMatchObject({ key: "claims", label: "Claim alignment", state: null, score: 75, hasConcern: true });
  });
});
