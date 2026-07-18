import type { AnalysisReport } from "./domain";

export type ExampleManifestEntry = {
  id: string;
  title: string;
  year: number;
  identifier: string;
  profile: string;
  content_level: string;
  coverage: number;
  concern_count: number;
  report: string;
  audit: string;
};

export type ExampleManifest = {
  schema_version: "1.0";
  generated_at: string;
  disclosure: string;
  examples: ExampleManifestEntry[];
};

export const STATIC_SHOWCASE = import.meta.env.VITE_STATIC_SHOWCASE === "true";

export function showcaseAssetUrl(path: string): string {
  return `${import.meta.env.BASE_URL}${path.replace(/^\//, "")}`;
}

export function exampleIdFromSearch(search: string): string | null {
  return new URLSearchParams(search).get("example");
}

export function exampleHref(id: string): string {
  return `?${new URLSearchParams({ example: id })}`;
}

export async function fetchExampleManifest(fetcher: typeof fetch = fetch): Promise<ExampleManifest> {
  const response = await fetcher(showcaseAssetUrl("examples/manifest.json"));
  if (!response.ok) throw new Error("The example gallery could not be loaded.");
  return response.json() as Promise<ExampleManifest>;
}

export async function fetchExampleReport(
  example: ExampleManifestEntry,
  fetcher: typeof fetch = fetch,
): Promise<AnalysisReport> {
  const response = await fetcher(showcaseAssetUrl(`examples/${example.report}`));
  if (!response.ok) throw new Error("This precomputed example could not be loaded.");
  return response.json() as Promise<AnalysisReport>;
}
