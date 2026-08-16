export type InvestigationStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export type Investigation = {
  id: string;
  project_id: string;
  project_name: string;
  github_repository_full_name: string;
  title: string;
  description: string | null;
  status: InvestigationStatus;
  created_at: string;
};

export type EvidenceKind = "recording" | "logs";

export type InvestigationEvidence = {
  id: string;
  kind: EvidenceKind;
  mime_type: string | null;
  filename: string | null;
  size_bytes: number | null;
  text_content: string | null;
  created_at: string;
};

export type BugAnalysis = {
  summary: string;
  observed_behavior: string;
  expected_behavior: string | null;
  reproduction_steps: string[];
  error_signals: string[];
  suspected_components: string[];
  confidence: "low" | "medium" | "high";
  needs_more_information: boolean;
  missing_information: string[];
};

export type AnalysisStatus = {
  investigation_id: string;
  status: InvestigationStatus;
  analysis: BugAnalysis | null;
};

export type BrowserAction =
  | { type: "goto"; path: string }
  | { type: "click"; selector: string }
  | { type: "fill"; selector: string; value: string }
  | { type: "press"; selector: string; key: string }
  | { type: "wait_for"; selector: string }
  | { type: "expect_text"; selector: string; value: string }
  | { type: "expect_visible"; selector: string }
  | { type: "expect_url"; value: string };

export type BrowserTestPlan = {
  name: string;
  start_path: string;
  actions: BrowserAction[];
};

export type AgentRunResult = {
  repository_findings: Array<{
    path: string;
    reason: string;
    observation: string;
  }>;
  duplicate_candidates: Array<{
    issue_number: number;
    title: string;
    url: string;
    similarity: "low" | "medium" | "high";
    reason: string;
  }>;
  reproduction_plan: BrowserTestPlan | null;
  generated_test: string | null;
  reproduction_status: "reproduced" | "not_reproduced" | "blocked" | null;
  execution: {
    status: "reproduced" | "not_reproduced" | "blocked";
    completed_actions: number;
    failed_action_index: number | null;
    expected: string | null;
    actual: string | null;
    summary: string;
  } | null;
  execution_summary: string | null;
};

export type AgentRunStatus = {
  investigation_id: string;
  status: "running" | "completed" | "failed" | null;
  result: AgentRunResult | null;
};
