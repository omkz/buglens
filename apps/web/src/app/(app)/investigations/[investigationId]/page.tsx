"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import { EvidenceRecorder } from "../_components/evidence-recorder";
import type {
  AnalysisStatus,
  BugAnalysis,
  Investigation,
  InvestigationEvidence,
} from "../_components/types";

type DetailState =
  | { status: "loading"; investigation: null; evidence: []; analysis: null }
  | {
      status: "ready";
      investigation: Investigation;
      evidence: InvestigationEvidence[];
      analysis: BugAnalysis | null;
    }
  | {
      status: "error";
      investigation: null;
      evidence: [];
      analysis: null;
      message: string;
    };

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
    analysis: null,
  });
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadInvestigation() {
      try {
        const encodedId = encodeURIComponent(investigationId);
        const [investigationResponse, evidenceResponse, analysisResponse] =
          await Promise.all([
            fetch(`${API_BASE_URL}/investigations/${encodedId}`, {
              credentials: "include",
            }),
            fetch(`${API_BASE_URL}/investigations/${encodedId}/evidence`, {
              credentials: "include",
            }),
            fetch(`${API_BASE_URL}/investigations/${encodedId}/analysis`, {
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
        const analysisBody = (await analysisResponse.json().catch(() => null)) as
          | AnalysisStatus
          | { detail?: string }
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
        if (
          !analysisResponse.ok ||
          !analysisBody ||
          !("status" in analysisBody)
        ) {
          throw new Error(
            analysisBody && "detail" in analysisBody
              ? analysisBody.detail
              : "Unable to load bug analysis.",
          );
        }
        if (!body || !("id" in body)) {
          throw new Error("Unable to load the investigation.");
        }
        if (!cancelled) {
          setState({
            status: "ready",
            investigation: { ...body, status: analysisBody.status },
            evidence: evidenceBody.evidence,
            analysis: analysisBody.analysis,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            investigation: null,
            evidence: [],
            analysis: null,
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

  async function analyzeBug() {
    if (state.status !== "ready" || isAnalyzing) return;
    setIsAnalyzing(true);
    setAnalysisError(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/analyze`,
        { method: "POST", credentials: "include" },
      );
      const body = (await response.json().catch(() => null)) as
        | AnalysisStatus
        | { detail?: string }
        | null;
      if (!response.ok || !body || !("status" in body)) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "Bug analysis failed. Please try again.",
        );
      }
      setState((current) =>
        current.status === "ready"
          ? {
              ...current,
              investigation: {
                ...current.investigation,
                status: body.status,
              },
              analysis: body.analysis,
            }
          : current,
      );
    } catch (error) {
      setAnalysisError(
        error instanceof Error
          ? error.message
          : "Bug analysis failed. Please try again.",
      );
      try {
        const response = await fetch(
          `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/analysis`,
          { credentials: "include" },
        );
        const body = (await response.json().catch(() => null)) as
          | AnalysisStatus
          | null;
        if (response.ok && body) {
          setState((current) =>
            current.status === "ready"
              ? {
                  ...current,
                  investigation: {
                    ...current.investigation,
                    status: body.status,
                  },
                  analysis: body.analysis,
                }
              : current,
          );
        }
      } catch {
        // Keep the last factual state; a refresh will reload persisted status.
      }
    } finally {
      setIsAnalyzing(false);
    }
  }

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

            {state.investigation.status === "pending" && !isAnalyzing && (
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

          <section className="flex flex-col gap-5 border-t border-zinc-800 pt-8">
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-medium text-zinc-100">
                Bug analysis
              </h2>
              <p className="text-sm text-zinc-500">
                Understand the supplied evidence without inspecting source code.
              </p>
            </div>

            {state.investigation.status === "pending" &&
              state.evidence.length > 0 &&
              !isAnalyzing && (
                <button
                  type="button"
                  disabled={isAnalyzing}
                  onClick={analyzeBug}
                  className="self-start rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Analyze Bug
                </button>
              )}

            {state.investigation.status === "pending" &&
              state.evidence.length === 0 && (
                <p className="text-sm text-zinc-500">
                  Add evidence before analyzing this investigation.
                </p>
              )}

            {(state.investigation.status === "running" || isAnalyzing) && (
              <p className="text-sm text-zinc-300">Analyzing evidence…</p>
            )}

            {state.investigation.status === "failed" && !isAnalyzing && (
              <div className="flex flex-col items-start gap-3 rounded-lg border border-red-900/60 bg-red-950/30 p-5">
                <p className="text-sm text-red-300">Analysis failed.</p>
                <button
                  type="button"
                  disabled={isAnalyzing}
                  onClick={analyzeBug}
                  className="rounded-full border border-red-800 px-4 py-2 text-sm font-medium text-red-200 hover:border-red-600 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Retry Analysis
                </button>
              </div>
            )}

            {analysisError && (
              <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
                {analysisError}
              </p>
            )}

            {state.analysis && <AnalysisResult analysis={state.analysis} />}
          </section>
        </article>
      )}
    </div>
  );
}

function AnalysisResult({ analysis }: { analysis: BugAnalysis }) {
  return (
    <div className="flex flex-col gap-6 rounded-lg border border-zinc-800 p-6">
      <AnalysisText title="Summary" value={analysis.summary} />
      <AnalysisText
        title="Observed behavior"
        value={analysis.observed_behavior}
      />
      <AnalysisText
        title="Expected behavior"
        value={analysis.expected_behavior || "Not established by the evidence."}
      />
      <AnalysisList
        title="Reproduction steps"
        items={analysis.reproduction_steps}
        ordered
      />
      <AnalysisList title="Error signals" items={analysis.error_signals} />
      <AnalysisList
        title="Suspected components"
        items={analysis.suspected_components}
      />
      <AnalysisText title="Confidence" value={analysis.confidence} capitalize />
      {analysis.needs_more_information && (
        <AnalysisList
          title="More information needed"
          items={analysis.missing_information}
        />
      )}
    </div>
  );
}

function AnalysisText({
  title,
  value,
  capitalize = false,
}: {
  title: string;
  value: string;
  capitalize?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-zinc-200">{title}</h3>
      <p
        className={`whitespace-pre-wrap text-sm leading-6 text-zinc-400 ${capitalize ? "capitalize" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}

function AnalysisList({
  title,
  items,
  ordered = false,
}: {
  title: string;
  items: string[];
  ordered?: boolean;
}) {
  const List = ordered ? "ol" : "ul";
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-zinc-200">{title}</h3>
      {items.length > 0 ? (
        <List
          className={`space-y-1 pl-5 text-sm leading-6 text-zinc-400 ${ordered ? "list-decimal" : "list-disc"}`}
        >
          {items.map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
        </List>
      ) : (
        <p className="text-sm text-zinc-500">None identified.</p>
      )}
    </div>
  );
}
