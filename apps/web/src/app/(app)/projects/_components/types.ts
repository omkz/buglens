export type Project = {
  id: string;
  name: string;
  githubRepo: string;
  defaultBranch: string;
  appUrl?: string;
};

export type GitHubRepository = {
  id: number;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
};
