"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import { EvidenceRecorder } from "../_components/evidence-recorder";
import type {
  Investigation,
  InvestigationEvidence,
} from "../_components/types";

type DetailState =
  | { status: "loading"; investigation: null; evidence: [] }
  | {
      status: "ready";
      investigation: Investigation;
      evidence: InvestigationEvidence[];
    }
  | { status: "error"; investigation: null; evidence: []; message: string };

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "long",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatFileSize(value: number | null) {
  if (value === null) return "Unknown size";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function InvestigationDetailPage() {
  const { investigationId } = useParams<{ investigationId: string }>();
  const [state, setState] = useState<DetailState>({
    status: "loading",
    investigation: null,
    evidence: [],
  });

  useEffect(() => {
    let cancelled = false;

    async function loadInvestigation() {
      try {
        const encodedId = encodeURIComponent(investigationId);
        const [investigationResponse, evidenceResponse] = await Promise.all([
          fetch(`${API_BASE_URL}/investigations/${encodedId}`, {
            credentials: "include",
          }),
          fetch(`${API_BASE_URL}/investigations/${encodedId}/evidence`, {
            credentials: "include",
          }),
        ]);
        const body = (await investigationResponse.json().catch(() => null)) as
          | Investigation
          | { detail?: string }
          | null;
        const evidenceBody = (await evidenceResponse.json().catch(() => null)) as
          | { evidence?: InvestigationEvidence[]; detail?: string }
          | null;
        if (!investigationResponse.ok) {
          throw new Error(
            body && "detail" in body && body.detail
              ? body.detail
              : "Unable to load the investigation.",
          );
        }
        if (!evidenceResponse.ok || !evidenceBody?.evidence) {
          throw new Error(
            evidenceBody?.detail ?? "Unable to load investigation evidence.",
          );
        }
        if (!body || !("id" in body)) {
          throw new Error("Unable to load the investigation.");
        }
        if (!cancelled) {
          setState({
            status: "ready",
            investigation: body,
            evidence: evidenceBody.evidence,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            investigation: null,
            evidence: [],
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

          <section className="flex flex-col gap-5">
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-medium text-zinc-100">Evidence</h2>
              <p className="text-sm text-zinc-500">
                Attach a screen recording, relevant logs, or both.
              </p>
            </div>

            {state.evidence.length === 0 && (
              <p className="text-sm text-zinc-500">
                No evidence has been saved yet.
              </p>
            )}

            {state.evidence.map((item) => (
              <article
                key={item.id}
                className="flex flex-col gap-3 rounded-lg border border-zinc-800 p-5"
              >
                {item.kind === "recording" ? (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium text-zinc-200">
                        {item.filename || "Screen recording"}
                      </p>
                      <p className="text-xs text-zinc-600">
                        {formatFileSize(item.size_bytes)}
                      </p>
                    </div>
                    <video
                      controls
                      preload="metadata"
                      crossOrigin="use-credentials"
                      src={`${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/evidence/${encodeURIComponent(item.id)}/content`}
                      className="max-h-96 w-full rounded-md bg-black"
                    />
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium text-zinc-200">Logs</p>
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-zinc-950 p-4 text-xs leading-5 text-zinc-400">
                      {item.text_content}
                    </pre>
                  </>
                )}
              </article>
            ))}

            {state.investigation.status === "pending" && (
              <EvidenceRecorder
                investigationId={investigationId}
                onSaved={(evidence) =>
                  setState((current) =>
                    current.status === "ready"
                      ? {
                          ...current,
                          evidence: [...current.evidence, ...evidence],
                        }
                      : current,
                  )
                }
              />
            )}
          </section>
        </article>
      )}
    </div>
  );
}
