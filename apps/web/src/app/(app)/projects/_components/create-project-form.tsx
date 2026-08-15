"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { API_BASE_URL } from "@/lib/config";
import { RepositorySelector } from "./repository-selector";
import type { GitHubRepository, Project } from "./types";

const inputClass =
  "rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-50 placeholder:text-zinc-600 outline-none focus:border-zinc-600";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-2 text-sm text-zinc-400">
      {label}
      {children}
    </label>
  );
}

export function CreateProjectForm({
  onCreate,
  onCancel,
}: {
  onCreate: (project: Project) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [appUrl, setAppUrl] = useState("");
  const [selectedRepository, setSelectedRepository] =
    useState<GitHubRepository | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedRepository) {
      setError("Select a GitHub repository.");
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          github_repository_id: selectedRepository.id,
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
            : "Unable to create the project.",
        );
      }
      if (!body || !("id" in body)) {
        throw new Error("Unable to create the project.");
      }

      setName("");
      setAppUrl("");
      setSelectedRepository(null);
      onCreate(body);
    } catch (submissionError) {
      setError(
        submissionError instanceof Error
          ? submissionError.message
          : "Unable to create the project.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-5 rounded-lg border border-zinc-800 p-6"
    >
      <Field label="Project name">
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="My App"
          className={inputClass}
        />
      </Field>

      <RepositorySelector
        selectedRepositoryId={selectedRepository?.id ?? null}
        onSelect={(repository) => {
          setSelectedRepository(repository);
          setError(null);
        }}
      />

      <Field label="App URL (optional)">
        <input
          type="url"
          value={appUrl}
          onChange={(e) => setAppUrl(e.target.value)}
          placeholder="https://app.example.com"
          className={inputClass}
        />
      </Field>

      {error && (
        <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={isSubmitting}
          className="rounded-full px-4 py-2 text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-200"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={isSubmitting || !selectedRepository}
          className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? "Creating…" : "Create Project"}
        </button>
      </div>
    </form>
  );
}
