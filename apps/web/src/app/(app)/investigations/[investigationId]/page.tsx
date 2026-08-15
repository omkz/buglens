"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import type { Investigation } from "../_components/types";

type DetailState =
  | { status: "loading"; investigation: null }
  | { status: "ready"; investigation: Investigation }
  | { status: "error"; investigation: null; message: string };

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "long",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function InvestigationDetailPage() {
  const { investigationId } = useParams<{ investigationId: string }>();
  const [state, setState] = useState<DetailState>({
    status: "loading",
    investigation: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function loadInvestigation() {
      try {
        const response = await fetch(
          `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}`,
          { credentials: "include" },
        );
        const body = (await response.json().catch(() => null)) as
          | Investigation
          | { detail?: string }
          | null;
        if (!response.ok) {
          throw new Error(
            body && "detail" in body && body.detail
              ? body.detail
              : "Unable to load the investigation.",
          );
        }
        if (!body || !("id" in body)) {
          throw new Error("Unable to load the investigation.");
        }
        if (!cancelled) {
          setState({ status: "ready", investigation: body });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            investigation: null,
            message:
              error instanceof Error
                ? error.message
                : "Unable to load the investigation.",
          });
        }
      }
    }

    loadInvestigation();
    return () => {
      cancelled = true;
    };
  }, [investigationId]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <Link
        href="/investigations"
        className="text-sm text-zinc-500 transition-colors hover:text-zinc-200"
      >
        ← Back to Investigations
      </Link>

      {state.status === "loading" && (
        <p className="text-sm text-zinc-500">Loading investigation…</p>
      )}

      {state.status === "error" && (
        <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
          {state.message}
        </p>
      )}

      {state.status === "ready" && (
        <article className="flex flex-col gap-8">
          <div className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-4">
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
                {state.investigation.title}
              </h1>
              <span className="rounded-full border border-zinc-700 px-3 py-1 text-xs capitalize text-zinc-400">
                {state.investigation.status}
              </span>
            </div>
            <p className="text-sm text-zinc-500">
              {`${state.investigation.project_name} · ${state.investigation.github_repository_full_name}`}
            </p>
            <p className="text-xs text-zinc-600">
              Created {formatCreatedAt(state.investigation.created_at)}
            </p>
          </div>

          <section className="flex flex-col gap-2 rounded-lg border border-zinc-800 p-5">
            <h2 className="text-sm font-medium text-zinc-200">
              Additional context
            </h2>
            <p className="whitespace-pre-wrap text-sm leading-6 text-zinc-400">
              {state.investigation.description ||
                "No additional context provided."}
            </p>
          </section>

          {state.investigation.status === "pending" && (
            <p className="rounded-lg border border-zinc-800 bg-zinc-900/30 px-5 py-4 text-sm text-zinc-400">
              Evidence collection will be added next.
            </p>
          )}
        </article>
      )}
    </div>
  );
}
