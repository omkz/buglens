export type Project = {
  id: string;
  name: string;
  github_repository_id: number;
  github_repository_full_name: string;
  default_branch: string;
  app_url: string | null;
  created_at: string;
};

export type GitHubRepository = {
  id: number;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
};
