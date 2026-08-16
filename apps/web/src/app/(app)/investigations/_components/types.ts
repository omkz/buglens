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
