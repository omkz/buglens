"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import { CreateProjectForm } from "./_components/create-project-form";
import { EmptyState } from "./_components/empty-state";
import {
  GithubConnection,
  type GithubConnectionInfo,
} from "./_components/github-connection";
import { ProjectCard } from "./_components/project-card";
import type { Project } from "./_components/types";

const GITHUB_ERROR_MESSAGES: Record<string, string> = {
  invalid_state:
    "That GitHub connection request expired or was invalid. Please try again.",
  missing_code:
    "GitHub did not return an authorization code. Please try again.",
  oauth_failed:
    "We couldn't complete the GitHub authorization. Please try again.",
  app_not_installed:
    "Install the BugLens GitHub App for your account, then try connecting again.",
};

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [githubInfo, setGithubInfo] = useState<GithubConnectionInfo>({
    status: "loading",
    accountLogin: null,
  });
  const [githubError] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const errorCode = new URLSearchParams(window.location.search).get(
      "github_error",
    );
    if (!errorCode) return null;
    return (
      GITHUB_ERROR_MESSAGES[errorCode] ??
      "GitHub connection failed. Please try again."
    );
  });

  useEffect(() => {
    if (!githubError) return;
    const params = new URLSearchParams(window.location.search);
    if (!params.has("github_error")) return;
    params.delete("github_error");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      query ? `?${query}` : window.location.pathname,
    );
  }, [githubError]);

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const response = await fetch(`${API_BASE_URL}/github/status`, {
          credentials: "include",
        });
        const data = (await response.json()) as {
          connected: boolean;
          account_login: string | null;
        };
        if (!cancelled) {
          setGithubInfo({
            status: data.connected ? "connected" : "disconnected",
            accountLogin: data.account_login,
          });
        }
      } catch {
        if (!cancelled) {
          setGithubInfo({ status: "disconnected", accountLogin: null });
        }
      }
    }

    loadStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  function handleCreate(project: Project) {
    setProjects((prev) => [...prev, project]);
    setIsCreating(false);
  }

  const isGithubConnected = githubInfo.status === "connected";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
          Projects
        </h1>
        {isGithubConnected && !isCreating && (
          <button
            type="button"
            onClick={() => setIsCreating(true)}
            className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
          >
            Create Project
          </button>
        )}
      </div>

      {githubError && (
        <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
          {githubError}
        </p>
      )}

      <GithubConnection info={githubInfo} />

      {isGithubConnected &&
        (isCreating ? (
          <CreateProjectForm
            onCreate={handleCreate}
            onCancel={() => setIsCreating(false)}
          />
        ) : projects.length === 0 ? (
          <EmptyState onCreate={() => setIsCreating(true)} />
        ) : (
          <ul className="flex flex-col gap-3">
            {projects.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </ul>
        ))}
    </div>
  );
}
