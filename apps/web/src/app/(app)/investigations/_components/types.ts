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
