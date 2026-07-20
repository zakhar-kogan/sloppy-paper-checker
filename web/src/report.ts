import type { AnalysisReport } from "./domain";

type Finding = AnalysisReport["findings"][number];
type ModuleStatus = NonNullable<AnalysisReport["module_statuses"]>[number];

const concernGrades = new Set<Finding["grade"]>([
  "critical_concern",
  "major_concern",
  "minor_concern",
]);

const gradeRank: Record<Finding["grade"], number> = {
  critical_concern: 0,
  major_concern: 1,
  minor_concern: 2,
  not_assessed: 3,
  no_concern: 4,
};

const legacyGradeSuffix = /:\s*(?:no concern|minor concern|major concern|critical concern|not assessed)\s*$/i;

export type AssessmentGroup = {
  key: string;
  label: string;
  state: ModuleStatus["state"] | null;
  limitation: string | null;
  assessedItems: number;
  expectedItems: number;
  gapCount: number;
  concernCount: number;
  concernLabel: string;
  hasConcern: boolean;
  score: number | null;
  items: Finding[];
};

const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export function findingDisplayTitle(finding: Finding): string {
  const cleaned = finding.title.replace(legacyGradeSuffix, "").trim();
  return cleaned || titleCase(finding.rubric_item);
}

export function majorTakeaways(report: AnalysisReport): Finding[] {
  return sortedFindings(
    report.findings.filter(
      (finding) => finding.critic_disposition !== "discarded" && concernGrades.has(finding.grade),
    ),
  ).slice(0, 4);
}

export function moduleStateLabel(state: AssessmentGroup["state"]): string {
  switch (state) {
    case "completed":
      return "Review completed";
    case "module_failed":
      return "Review failed";
    case "ineligible_at_content_level":
      return "Unavailable for this content";
    case "unreviewed":
      return "Review incomplete";
    default:
      return "Review status unavailable";
  }
}

export function coverageStateLabel(provisional: boolean): string {
  return provisional ? "Provisional coverage" : "Final coverage";
}

function sortedFindings(findings: Finding[]): Finding[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort((left, right) => gradeRank[left.finding.grade] - gradeRank[right.finding.grade] || left.index - right.index)
    .map(({ finding }) => finding);
}

function concernLabel(concernCount: number, assessedItems: number, expectedItems: number): string {
  if (concernCount > 0) return `${concernCount} concern${concernCount === 1 ? "" : "s"}`;
  if (assessedItems === 0) return "No items assessed";
  if (assessedItems < expectedItems) return "No concerns in assessed items";
  return "No concerns";
}

export function buildAssessmentGroups(report: AnalysisReport): AssessmentGroup[] {
  const findings = report.findings.filter((finding) => finding.critic_disposition !== "discarded");
  const dimensions = new Map(report.dimensions.map((dimension) => [dimension.key, dimension]));
  const findingsByCategory = new Map<string, Finding[]>();

  findings.forEach((finding) => {
    const categoryFindings = findingsByCategory.get(finding.category) ?? [];
    categoryFindings.push(finding);
    findingsByCategory.set(finding.category, categoryFindings);
  });

  const groups: AssessmentGroup[] = [];
  const knownCategories = new Set<string>();

  (report.module_statuses ?? []).forEach((module) => {
    const items = sortedFindings(findingsByCategory.get(module.key) ?? []);
    const concernCount = items.filter((finding) => concernGrades.has(finding.grade)).length;
    const dimension = dimensions.get(module.key);
    knownCategories.add(module.key);
    groups.push({
      key: module.key,
      label: module.label,
      state: module.state,
      limitation: module.limitation ?? null,
      assessedItems: module.assessed_items,
      expectedItems: module.expected_items,
      gapCount: Math.max(0, module.expected_items - module.assessed_items),
      concernCount,
      concernLabel: concernLabel(concernCount, module.assessed_items, module.expected_items),
      hasConcern: concernCount > 0,
      score: module.assessed_items > 0 && dimension ? dimension.score : null,
      items,
    });
  });

  findingsByCategory.forEach((categoryFindings, key) => {
    if (knownCategories.has(key)) return;
    const items = sortedFindings(categoryFindings);
    const assessedItems = items.filter((finding) => finding.grade !== "not_assessed").length;
    const concernCount = items.filter((finding) => concernGrades.has(finding.grade)).length;
    const dimension = dimensions.get(key);
    groups.push({
      key,
      label: dimension?.label ?? titleCase(key),
      state: null,
      limitation: null,
      assessedItems,
      expectedItems: items.length,
      gapCount: items.length - assessedItems,
      concernCount,
      concernLabel: concernLabel(concernCount, assessedItems, items.length),
      hasConcern: concernCount > 0,
      score: assessedItems > 0 && dimension ? dimension.score : null,
      items,
    });
  });

  return groups;
}
