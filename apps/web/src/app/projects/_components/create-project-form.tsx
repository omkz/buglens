"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import type { Project } from "./types";

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
  const [githubRepo, setGithubRepo] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [appUrl, setAppUrl] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onCreate({
      id: crypto.randomUUID(),
      name: name.trim(),
      githubRepo: githubRepo.trim(),
      defaultBranch: defaultBranch.trim() || "main",
      appUrl: appUrl.trim() || undefined,
    });
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

      <Field label="GitHub repository">
        <input
          required
          value={githubRepo}
          onChange={(e) => setGithubRepo(e.target.value)}
          placeholder="owner/repo"
          className={inputClass}
        />
      </Field>

      <Field label="Default branch">
        <input
          required
          value={defaultBranch}
          onChange={(e) => setDefaultBranch(e.target.value)}
          placeholder="main"
          className={inputClass}
        />
      </Field>

      <Field label="App URL (optional)">
        <input
          type="url"
          value={appUrl}
          onChange={(e) => setAppUrl(e.target.value)}
          placeholder="https://app.example.com"
          className={inputClass}
        />
      </Field>

      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-full px-4 py-2 text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-200"
        >
          Cancel
        </button>
        <button
          type="submit"
          className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
        >
          Create Project
        </button>
      </div>
    </form>
  );
}
