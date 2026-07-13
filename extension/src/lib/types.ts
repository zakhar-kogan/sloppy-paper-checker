export type PaperCandidate = {
  kind: "doi" | "url";
  value: string;
  title?: string;
  authors?: string[];
  isPdf: boolean;
  captureLimitation?: string;
};

export type AnalysisStatus = {
  id: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  stage: string;
  error?: string;
};

export type Finding = {
  id: string;
  category: string;
  rubric_item: string;
  title: string;
  explanation: string;
  severity: "info" | "minor" | "major" | "critical";
  grade: string;
  confidence: number;
  paper_spans: Array<{section?: string; page?: number; quote: string}>;
  external_sources: Array<{title: string; url: string; publisher?: string}>;
  affected_conclusions: string[];
  counterevidence: string[];
  limitations: string[];
  critic_disposition: string;
};

export type AnalysisReport = {
  id: string;
  schema_version: string;
  scoring_version: string;
  identity: {doi?: string; title?: string; authors: string[]; journal?: string; fingerprint: string};
  profile: string;
  language: string;
  composite_score: number;
  uncapped_score: number;
  dimensions: Array<{key: string; label: string; weight: number; score: number; assessed_items: number; total_items: number}>;
  coverage: {paper: number; context: number; overall: number; provisional: boolean; limitations: string[]};
  context: {retracted: boolean; expression_of_concern: boolean; corrections: string[]};
  findings: Finding[];
  banners: string[];
  limitations: string[];
  audit_trail: Array<{at?: string; stage?: string; progress?: number}>;
  completed_at: string;
};

