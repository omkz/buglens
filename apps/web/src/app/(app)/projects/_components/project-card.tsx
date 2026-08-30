"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { API_BASE_URL } from "@/lib/config";
import { AsyncActivity } from "../../_components/async-activity";
import type { Project } from "./types";

const inputClass =
  "rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-50 placeholder:text-zinc-600 outline-none focus:border-zinc-600";

export function ProjectCard({
  project,
  onUpdate,
}: {
  project: Project;
  onUpdate: (project: Project) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [appUrl, setAppUrl] = useState(project.app_url ?? "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function beginEditing() {
    setName(project.name);
    setAppUrl(project.app_url ?? "");
    setError(null);
    setIsEditing(true);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          app_url: appUrl.trim() || null,
        }),
      });
      const body = (await response.json().catch(() => null)) as
        | Project
        | { detail?: string }
        | null;
      if (!response.ok) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "Unable to update the project.",
        );
      }
      if (!body || !("id" in body)) {
        throw new Error("Unable to update the project.");
      }

      onUpdate(body);
      setIsEditing(false);
    } catch (submissionError) {
      setError(
        submissionError instanceof Error
          ? submissionError.message
          : "Unable to update the project.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isEditing) {
    return (
      <li className="rounded-lg border border-zinc-800 px-5 py-4">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-2 text-sm text-zinc-400">
            Project name
            <input
              required
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className={inputClass}
            />
          </label>

          <div className="flex flex-col gap-1 text-sm text-zinc-400">
            <span>GitHub repository</span>
            <span className="rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-zinc-500">
              {project.github_repository_full_name}
            </span>
          </div>

          <label className="flex flex-col gap-2 text-sm text-zinc-400">
            App URL (optional)
            <input
              type="url"
              value={appUrl}
              onChange={(event) => setAppUrl(event.target.value)}
              placeholder="https://app.example.com"
              className={inputClass}
            />
            <span className="text-xs text-zinc-600">
              Add your deployed app URL to enable automated browser
              reproduction.
            </span>
          </label>

          {error && (
            <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-3 pt-1">
            <button
              type="button"
              onClick={() => setIsEditing(false)}
              disabled={isSubmitting}
              className="rounded-full px-4 py-2 text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !name.trim()}
              className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? (
                <AsyncActivity label="Saving project…" />
              ) : (
                "Save"
              )}
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-1 rounded-lg border border-zinc-800 px-5 py-4">
      <span className="text-sm font-medium text-zinc-50">{project.name}</span>
      <span className="text-sm text-zinc-500">
        {project.github_repository_full_name}
      </span>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600">
        <span>Branch: {project.default_branch}</span>
        {project.app_url && <span>App URL: {project.app_url}</span>}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={beginEditing}
          className="rounded-full border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:border-zinc-500 hover:bg-zinc-800"
        >
          Edit Project
        </button>
        <Link
          href={`/projects/${project.id}/report`}
          className="rounded-full border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:border-zinc-500 hover:bg-zinc-800"
        >
          Report Bug
        </Link>
      </div>
    </li>
  );
}
