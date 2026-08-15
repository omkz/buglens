"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { API_BASE_URL } from "@/lib/config";
import type { Investigation } from "../../../investigations/_components/types";
import type { Project } from "../../_components/types";

type ProjectState =
  | { status: "loading"; project: null }
  | { status: "ready"; project: Project }
  | { status: "error"; project: null; message: string };

const inputClass =
  "rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-50 placeholder:text-zinc-600 outline-none focus:border-zinc-600";

export default function ReportBugPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const [projectState, setProjectState] = useState<ProjectState>({
    status: "loading",
    project: null,
  });
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadProject() {
      try {
        const response = await fetch(`${API_BASE_URL}/projects`, {
          credentials: "include",
        });
        const body = (await response.json().catch(() => null)) as
          | { projects?: Project[]; detail?: string }
          | null;
        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to load the Project.");
        }
        const project = body?.projects?.find((item) => item.id === projectId);
        if (!project) {
          throw new Error("Project not found.");
        }
        if (!cancelled) {
          setProjectState({ status: "ready", project });
        }
      } catch (error) {
        if (!cancelled) {
          setProjectState({
            status: "error",
            project: null,
            message:
              error instanceof Error
                ? error.message
                : "Unable to load the Project.",
          });
        }
      }
    }

    loadProject();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (projectState.status !== "ready" || !title.trim()) return;

    setSubmitError(null);
    setIsSubmitting(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/investigations`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title.trim(),
            description: description.trim() || null,
          }),
        },
      );
      const body = (await response.json().catch(() => null)) as
        | Investigation
        | { detail?: string }
        | null;
      if (!response.ok) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "Unable to create the investigation.",
        );
      }
      if (!body || !("id" in body)) {
        throw new Error("Unable to create the investigation.");
      }
      router.push(`/investigations/${encodeURIComponent(body.id)}`);
    } catch (error) {
      setSubmitError(
        error instanceof Error
          ? error.message
          : "Unable to create the investigation.",
      );
      setIsSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <Link
        href="/projects"
        className="text-sm text-zinc-500 transition-colors hover:text-zinc-200"
      >
        ← Back to Projects
      </Link>

      {projectState.status === "loading" && (
        <p className="text-sm text-zinc-500">Loading Project…</p>
      )}

      {projectState.status === "error" && (
        <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
          {projectState.message}
        </p>
      )}

      {projectState.status === "ready" && (
        <div className="flex flex-col gap-8">
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
              Report Bug
            </h1>
            <p className="text-sm text-zinc-400">
              {projectState.project.name}
            </p>
            <p className="text-sm text-zinc-600">
              {projectState.project.github_repository_full_name}
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            className="flex flex-col gap-5 rounded-lg border border-zinc-800 p-6"
          >
            <label className="flex flex-col gap-2 text-sm text-zinc-400">
              Bug title
              <input
                required
                maxLength={255}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Checkout button does nothing"
                className={inputClass}
              />
            </label>

            <label className="flex flex-col gap-2 text-sm text-zinc-400">
              Additional context (optional)
              <textarea
                maxLength={10000}
                rows={6}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Happens after adding an item to cart."
                className={inputClass}
              />
            </label>

            {submitError && (
              <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
                {submitError}
              </p>
            )}

            <div className="flex justify-end pt-2">
              <button
                type="submit"
                disabled={isSubmitting || !title.trim()}
                className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? "Creating…" : "Create Investigation"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
