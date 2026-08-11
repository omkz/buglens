"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import type { GitHubRepository } from "./types";

type RepositoryState =
  | { status: "loading"; repositories: GitHubRepository[] }
  | { status: "ready"; repositories: GitHubRepository[] }
  | { status: "error"; repositories: GitHubRepository[]; message: string };

export function RepositorySelector() {
  const [repositoryState, setRepositoryState] = useState<RepositoryState>({
    status: "loading",
    repositories: [],
  });
  const [selectedRepositoryId, setSelectedRepositoryId] = useState<
    number | null
  >(null);

  useEffect(() => {
    let cancelled = false;

    async function loadRepositories() {
      try {
        const response = await fetch(`${API_BASE_URL}/github/repositories`, {
          credentials: "include",
        });
        const body = (await response.json().catch(() => null)) as {
          repositories?: GitHubRepository[];
          detail?: string;
        } | null;

        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to load GitHub repositories.");
        }
        if (!body || !Array.isArray(body.repositories)) {
          throw new Error("Unable to load GitHub repositories.");
        }
        if (!cancelled) {
          setRepositoryState({
            status: "ready",
            repositories: body.repositories,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setRepositoryState({
            status: "error",
            repositories: [],
            message:
              error instanceof Error
                ? error.message
                : "Unable to load GitHub repositories.",
          });
        }
      }
    }

    loadRepositories();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-800 p-5">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-medium text-zinc-200">
          Select a GitHub repository
        </h2>
        <p className="text-sm text-zinc-500">
          Choose a repository for this setup. Your selection is not saved yet.
        </p>
      </div>

      {repositoryState.status === "loading" && (
        <p className="text-sm text-zinc-500">Loading repositories…</p>
      )}

      {repositoryState.status === "error" && (
        <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
          {repositoryState.message}
        </p>
      )}

      {repositoryState.status === "ready" &&
        repositoryState.repositories.length === 0 && (
          <p className="text-sm text-zinc-500">
            This GitHub App installation cannot access any repositories.
          </p>
        )}

      {repositoryState.repositories.length > 0 && (
        <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
          {repositoryState.repositories.map((repository) => {
            const isSelected = selectedRepositoryId === repository.id;
            return (
              <li key={repository.id}>
                <button
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => setSelectedRepositoryId(repository.id)}
                  className={`flex w-full items-center justify-between gap-4 rounded-md border px-4 py-3 text-left transition-colors ${
                    isSelected
                      ? "border-zinc-500 bg-zinc-800/70"
                      : "border-zinc-800 bg-zinc-950 hover:border-zinc-700"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-zinc-200">
                      {repository.full_name}
                    </span>
                    <span className="block text-xs text-zinc-500">
                      Default branch: {repository.default_branch}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-xs text-zinc-400">
                      {repository.private ? "Private" : "Public"}
                    </span>
                    {isSelected && (
                      <span className="text-xs font-medium text-emerald-400">
                        Selected
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
