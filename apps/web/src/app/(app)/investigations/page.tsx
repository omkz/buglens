"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import type { Investigation } from "./_components/types";

type InvestigationsState =
  | { status: "loading"; investigations: Investigation[] }
  | { status: "ready"; investigations: Investigation[] }
  | { status: "error"; investigations: Investigation[]; message: string };

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function InvestigationsPage() {
  const [state, setState] = useState<InvestigationsState>({
    status: "loading",
    investigations: [],
  });

  useEffect(() => {
    let cancelled = false;

    async function loadInvestigations() {
      try {
        const response = await fetch(`${API_BASE_URL}/investigations`, {
          credentials: "include",
        });
        const body = (await response.json().catch(() => null)) as
          | { investigations?: Investigation[]; detail?: string }
          | null;
        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to load investigations.");
        }
        if (!body || !Array.isArray(body.investigations)) {
          throw new Error("Unable to load investigations.");
        }
        if (!cancelled) {
          setState({ status: "ready", investigations: body.investigations });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            investigations: [],
            message:
              error instanceof Error
                ? error.message
                : "Unable to load investigations.",
          });
        }
      }
    }

    loadInvestigations();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
        Investigations
      </h1>

      {state.status === "loading" && (
        <p className="text-sm text-zinc-500">Loading investigations…</p>
      )}

      {state.status === "error" && (
        <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
          {state.message}
        </p>
      )}

      {state.status === "ready" && state.investigations.length === 0 && (
        <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed border-zinc-800 px-6 py-16 text-center">
          <p className="text-base font-medium text-zinc-200">
            No investigations yet
          </p>
          <p className="max-w-sm text-sm text-zinc-500">
            Report a bug from one of your Projects to create an investigation.
          </p>
          <Link
            href="/projects"
            className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
          >
            View Projects
          </Link>
        </div>
      )}

      {state.investigations.length > 0 && (
        <ul className="flex flex-col gap-3">
          {state.investigations.map((investigation) => (
            <li key={investigation.id}>
              <Link
                href={`/investigations/${investigation.id}`}
                className="flex flex-col gap-3 rounded-lg border border-zinc-800 px-5 py-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900/40"
              >
                <div className="flex items-start justify-between gap-4">
                  <span className="text-sm font-medium text-zinc-50">
                    {investigation.title}
                  </span>
                  <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-xs capitalize text-zinc-400">
                    {investigation.status}
                  </span>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
                  <span>{investigation.project_name}</span>
                  <span>{formatCreatedAt(investigation.created_at)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
