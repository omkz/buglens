"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import { EvidenceRecorder } from "../_components/evidence-recorder";
import { shouldShowAnalyzeBug } from "@/lib/analysis-availability.mjs";
import type {
  AgentRunResult,
  AgentRunProgress,
  AgentRunStatus,
  AnalysisStatus,
  BrowserAction,
  BugAnalysis,
  GitHubIssue,
  GitHubIssuePublication,
  Investigation,
  InvestigationEvidence,
} from "../_components/types";

type DetailState =
  | {
      status: "loading";
      investigation: null;
      evidence: [];
      analysis: null;
      agentRun: null;
    }
  | {
      status: "ready";
      investigation: Investigation;
      evidence: InvestigationEvidence[];
      analysis: BugAnalysis | null;
      agentRun: AgentRunStatus;
    }
  | {
      status: "error";
      investigation: null;
      evidence: [];
      analysis: null;
      agentRun: null;
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

function progressStageLabel(stage: AgentRunProgress["stage"] | undefined) {
  if (!stage) return "Starting investigation";
  return {
    starting: "Starting investigation",
    investigating_repository: "Investigating repository",
    searching_duplicates: "Searching duplicate issues",
    preparing_reproduction: "Preparing browser reproduction",
    running_browser: "Running browser reproduction",
    completed: "Investigation completed",
    failed: "Investigation failed",
  }[stage];
}

function isAgentRunProgressStage(
  value: string,
): value is AgentRunProgress["stage"] {
  return [
    "starting",
    "investigating_repository",
    "searching_duplicates",
    "preparing_reproduction",
    "running_browser",
    "completed",
    "failed",
  ].includes(value);
}

export default function InvestigationDetailPage() {
  const { investigationId } = useParams<{ investigationId: string }>();
  const [state, setState] = useState<DetailState>({
    status: "loading",
    investigation: null,
    evidence: [],
    analysis: null,
    agentRun: null,
  });
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [isInvestigating, setIsInvestigating] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(
    null,
  );
  const [liveProgressDisconnected, setLiveProgressDisconnected] =
    useState(false);
  const [isCreatingIssue, setIsCreatingIssue] = useState(false);
  const [issueError, setIssueError] = useState<string | null>(null);
  const issueRequestActiveRef = useRef(false);
  const investigationRequestActiveRef = useRef(false);
  const progressSourceRef = useRef<EventSource | null>(null);
  const latestAgentRunAttemptRef = useRef<string | null>(null);

  useEffect(() => {
    latestAgentRunAttemptRef.current = null;
    return () => {
      progressSourceRef.current?.close();
      progressSourceRef.current = null;
    };
  }, [investigationId]);

  useEffect(() => {
    let cancelled = false;

    async function loadInvestigation() {
      try {
        const encodedId = encodeURIComponent(investigationId);
        const [
          investigationResponse,
          evidenceResponse,
          analysisResponse,
          agentRunResponse,
        ] =
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
            fetch(`${API_BASE_URL}/investigations/${encodedId}/agent-run`, {
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
        const agentRunBody = (await agentRunResponse.json().catch(() => null)) as
          | AgentRunStatus
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
        if (
          !agentRunResponse.ok ||
          !agentRunBody ||
          !("status" in agentRunBody)
        ) {
          throw new Error(
            agentRunBody && "detail" in agentRunBody && agentRunBody.detail
              ? agentRunBody.detail
              : "Unable to load the investigation result.",
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
            agentRun: agentRunBody,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            investigation: null,
            evidence: [],
            analysis: null,
            agentRun: null,
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

  async function refreshAgentRunStatus(expectedAttemptId: string) {
    const response = await fetch(
      `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/agent-run`,
      { credentials: "include" },
    );
    const body = (await response.json().catch(() => null)) as
      | AgentRunStatus
      | { detail?: string }
      | null;
    if (!response.ok || !body || !("status" in body)) {
      throw new Error(
        body && "detail" in body && body.detail
          ? body.detail
          : "Unable to load the investigation result.",
      );
    }
    setState((current) => {
      if (current.status !== "ready") return current;
      if (
        latestAgentRunAttemptRef.current !== expectedAttemptId ||
        body.attempt_id !== expectedAttemptId
      ) {
        return current;
      }
      return { ...current, agentRun: body };
    });
    return body;
  }

  async function runInvestigation() {
    if (
      state.status !== "ready" ||
      isInvestigating ||
      investigationRequestActiveRef.current
    )
      return;
    investigationRequestActiveRef.current = true;
    setIsInvestigating(true);
    setInvestigationError(null);
    setLiveProgressDisconnected(false);

    const encodedId = encodeURIComponent(investigationId);
    const attemptId = crypto.randomUUID();
    latestAgentRunAttemptRef.current = attemptId;
    const progressSource = new EventSource(
      `${API_BASE_URL}/investigations/${encodedId}/agent-run/events?attempt_id=${encodeURIComponent(attemptId)}`,
      { withCredentials: true },
    );
    progressSourceRef.current = progressSource;

    const receiveProgress = (event: MessageEvent<string>) => {
      try {
        const progress = JSON.parse(event.data) as Partial<
          AgentRunProgress & { attempt_id: string }
        >;
        if (
          progress.attempt_id !== attemptId ||
          latestAgentRunAttemptRef.current !== attemptId ||
          typeof progress.stage !== "string" ||
          !isAgentRunProgressStage(progress.stage) ||
          typeof progress.message !== "string"
        ) {
          return false;
        }
        const stage = progress.stage;
        const message = progress.message;
        setState((current) =>
          current.status === "ready"
            ? {
                ...current,
                agentRun: {
                  ...current.agentRun,
                  progress: {
                    stage,
                    message,
                    updated_at: new Date().toISOString(),
                  },
                },
              }
            : current,
        );
        return true;
      } catch {
        // Ignore malformed transport data; the POST remains authoritative.
        return false;
      }
    };
    progressSource.addEventListener("progress", receiveProgress);
    progressSource.addEventListener("complete", (event) => {
      const accepted = receiveProgress(event as MessageEvent<string>);
      progressSource.close();
      if (progressSourceRef.current === progressSource) {
        progressSourceRef.current = null;
      }
      if (accepted) {
        void refreshAgentRunStatus(attemptId).catch(() => {
          // The POST remains authoritative if reconciliation is unavailable.
        });
      }
    });
    progressSource.addEventListener("failed", (event) => {
      const accepted = receiveProgress(event as MessageEvent<string>);
      progressSource.close();
      if (progressSourceRef.current === progressSource) {
        progressSourceRef.current = null;
      }
      if (accepted) {
        void refreshAgentRunStatus(attemptId).catch(() => {
          // The POST remains authoritative if reconciliation is unavailable.
        });
      }
    });
    progressSource.onerror = () => {
      if (progressSourceRef.current === progressSource) {
        progressSource.close();
        progressSourceRef.current = null;
        setLiveProgressDisconnected(true);
      }
    };

    try {
      const response = await fetch(
        `${API_BASE_URL}/investigations/${encodedId}/agent-run`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ attempt_id: attemptId }),
        },
      );
      const body = (await response.json().catch(() => null)) as
        | AgentRunStatus
        | { detail?: string }
        | null;
      if (!response.ok || !body || !("status" in body)) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "Autonomous investigation failed. Please try again.",
        );
      }
      setState((current) =>
        current.status === "ready" &&
        latestAgentRunAttemptRef.current === attemptId &&
        body.attempt_id === attemptId
          ? { ...current, agentRun: body }
          : current,
      );
    } catch (error) {
      setInvestigationError(
        error instanceof Error
          ? error.message
          : "Autonomous investigation failed. Please try again.",
      );
      try {
        await refreshAgentRunStatus(attemptId);
      } catch {
        // A refresh reloads the persisted run state.
      }
    } finally {
      progressSource.close();
      if (progressSourceRef.current === progressSource) {
        progressSourceRef.current = null;
      }
      investigationRequestActiveRef.current = false;
      setIsInvestigating(false);
    }
  }

  async function createGitHubIssue() {
    if (
      state.status !== "ready" ||
      state.agentRun.status !== "completed" ||
      state.agentRun.github_issue_status === "created" ||
      issueRequestActiveRef.current
    ) {
      return;
    }
    issueRequestActiveRef.current = true;
    setIsCreatingIssue(true);
    setIssueError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/github-issue`,
        { method: "POST", credentials: "include" },
      );
      const body = (await response.json().catch(() => null)) as
        | GitHubIssuePublication
        | { detail?: string }
        | null;
      if (!response.ok || !body || !("issue" in body)) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "GitHub issue creation failed. Please try again.",
        );
      }
      setState((current) =>
        current.status === "ready"
          ? {
              ...current,
              agentRun: {
                ...current.agentRun,
                github_issue_status: "created",
                github_issue: body.issue,
              },
            }
          : current,
      );
    } catch (error) {
      setIssueError(
        error instanceof Error
          ? error.message
          : "GitHub issue creation failed. Please try again.",
      );
      try {
        const response = await fetch(
          `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/agent-run`,
          { credentials: "include" },
        );
        const body = (await response.json().catch(() => null)) as
          | AgentRunStatus
          | null;
        if (response.ok && body) {
          if (body.github_issue_status === "created") setIssueError(null);
          setState((current) =>
            current.status === "ready"
              ? { ...current, agentRun: body }
              : current,
          );
        }
      } catch {
        // A refresh reloads the persisted publication state.
      }
    } finally {
      issueRequestActiveRef.current = false;
      setIsCreatingIssue(false);
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
              <p className="max-w-2xl text-sm leading-6 text-zinc-500">
                No evidence attached. Buglensa will analyze the report
                description. Add a recording or logs for richer context.
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
                Understand the report and any supplied evidence before inspecting
                source code.
              </p>
            </div>

            {shouldShowAnalyzeBug(state.investigation.status, isAnalyzing) && (
              <button
                type="button"
                disabled={isAnalyzing}
                onClick={analyzeBug}
                className="self-start rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Analyze Bug
              </button>
            )}

            {(state.investigation.status === "running" || isAnalyzing) && (
              <p className="text-sm text-zinc-300">
                Analyzing report and any evidence…
              </p>
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

          {state.analysis && state.investigation.status === "completed" && (
            <section className="flex flex-col gap-5 border-t border-zinc-800 pt-8">
              <div className="flex flex-col gap-1">
                <h2 className="text-lg font-medium text-zinc-100">
                  Investigation result
                </h2>
                <p className="text-sm text-zinc-500">
                  Inspect the connected repository, possible duplicate issues,
                  and a bounded browser reproduction.
                </p>
              </div>

              {(state.agentRun.status === null ||
                state.agentRun.status === "failed") &&
                !isInvestigating && (
                  <button
                    type="button"
                    onClick={runInvestigation}
                    className="self-start rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {state.agentRun.status === "failed"
                      ? "Retry Investigation"
                      : "Run Investigation"}
                  </button>
                )}

              {(state.agentRun.status === "running" || isInvestigating) && (
                <div className="flex flex-col gap-2 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                    Investigation progress
                  </p>
                  <p className="text-sm font-medium text-zinc-200">
                    {progressStageLabel(state.agentRun.progress?.stage)}
                  </p>
                  <p className="text-sm text-zinc-400">
                    {state.agentRun.progress?.message ||
                      "Starting investigation…"}
                  </p>
                  {liveProgressDisconnected && isInvestigating && (
                    <p className="text-xs text-amber-500/80">
                      Live progress disconnected. The investigation is still running.
                    </p>
                  )}
                </div>
              )}

              {state.agentRun.status === "failed" && !isInvestigating && (
                <p className="text-sm text-red-300">Investigation failed.</p>
              )}

              {state.agentRun.status === "completed" && !isInvestigating && (
                <p className="text-sm text-emerald-300">
                  Investigation completed.
                </p>
              )}

              {investigationError && (
                <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
                  {investigationError}
                </p>
              )}

              {state.agentRun.result && (
                <AgentRunResultView
                  result={state.agentRun.result}
                  githubIssueStatus={state.agentRun.github_issue_status}
                  githubIssue={state.agentRun.github_issue}
                  isCreatingIssue={isCreatingIssue}
                  issueError={issueError}
                  onCreateIssue={createGitHubIssue}
                />
              )}
            </section>
          )}
        </article>
      )}
    </div>
  );
}

function AgentRunResultView({
  result,
  githubIssueStatus,
  githubIssue,
  isCreatingIssue,
  issueError,
  onCreateIssue,
}: {
  result: AgentRunResult;
  githubIssueStatus: "creating" | "created" | "failed" | null;
  githubIssue: GitHubIssue | null;
  isCreatingIssue: boolean;
  issueError: string | null;
  onCreateIssue: () => void;
}) {
  const reproductionLabel = result.reproduction_status
    ? {
        reproduced: "Reproduced",
        not_reproduced: "Not reproduced",
        blocked: "Blocked",
      }[result.reproduction_status]
    : "Not attempted";

  return (
    <div className="flex flex-col gap-7 rounded-lg border border-zinc-800 p-6">
      <ResultList title="Repository findings" empty="No relevant files identified.">
        {result.repository_findings.map((finding) => (
          <li key={`${finding.path}-${finding.reason}`} className="space-y-1">
            <code className="text-zinc-200">{finding.path}</code>
            <p>{finding.observation}</p>
            <p className="text-xs text-zinc-500">{finding.reason}</p>
          </li>
        ))}
      </ResultList>

      <ResultList title="Possible duplicate issues" empty="No plausible duplicates found.">
        {result.duplicate_candidates.map((candidate) => (
          <li key={candidate.issue_number} className="space-y-1">
            <a
              href={candidate.url}
              target="_blank"
              rel="noreferrer"
              className="text-zinc-200 underline decoration-zinc-700 underline-offset-4 hover:decoration-zinc-300"
            >
              #{candidate.issue_number} {candidate.title}
            </a>
            <p className="text-xs capitalize text-zinc-500">
              {candidate.similarity} similarity
            </p>
            <p>{candidate.reason}</p>
          </li>
        ))}
      </ResultList>

      <div className="flex flex-col items-start gap-3 border-t border-zinc-800 pt-6">
        {githubIssueStatus === "created" && githubIssue ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium text-emerald-300">
              GitHub issue created
            </p>
            <p className="text-sm text-zinc-300">
              #{githubIssue.number} {githubIssue.title}
            </p>
            <a
              href={githubIssue.url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-zinc-200 underline decoration-zinc-700 underline-offset-4 hover:decoration-zinc-300"
            >
              Open on GitHub
            </a>
          </div>
        ) : githubIssueStatus === "creating" || isCreatingIssue ? (
          <p className="text-sm text-zinc-300">Creating GitHub issue…</p>
        ) : (
          <button
            type="button"
            disabled={isCreatingIssue}
            onClick={onCreateIssue}
            className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {githubIssueStatus === "failed"
              ? "Retry GitHub Issue"
              : "Create GitHub Issue"}
          </button>
        )}
        {issueError && (
          <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
            {issueError}
          </p>
        )}
      </div>

      {result.reproduction_plan && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-zinc-200">
            Reproduction plan
          </h3>
          <p className="text-sm text-zinc-400">
            {result.reproduction_plan.name}
          </p>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-400">
            {result.reproduction_plan.actions.map((action, index) => (
              <li key={`${index}-${action.type}`}>
                {describeBrowserAction(action)}
              </li>
            ))}
          </ol>
        </div>
      )}

      {result.generated_test && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-zinc-200">
            Generated Playwright test
          </h3>
          <pre className="max-h-96 overflow-auto rounded-md bg-zinc-950 p-4 text-xs leading-5 text-zinc-400">
            {result.generated_test}
          </pre>
        </div>
      )}

      <div className="flex flex-col gap-2 rounded-md bg-zinc-950 p-4">
        <h3 className="text-sm font-medium text-zinc-200">
          Reproduction result
        </h3>
        <p className="text-lg font-semibold text-zinc-100">
          {reproductionLabel}
        </p>
        {result.execution_summary && (
          <p className="text-sm leading-6 text-zinc-400">
            {result.execution_summary}
          </p>
        )}
      </div>
    </div>
  );
}

function ResultList({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode;
}) {
  const items = Array.isArray(children) ? children : [children];
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium text-zinc-200">{title}</h3>
      {items.length > 0 ? (
        <ul className="space-y-4 text-sm leading-6 text-zinc-400">
          {children}
        </ul>
      ) : (
        <p className="text-sm text-zinc-500">{empty}</p>
      )}
    </div>
  );
}

function describeBrowserAction(action: BrowserAction) {
  switch (action.type) {
    case "goto":
      return `Open ${action.path}`;
    case "click":
      return `Click ${action.selector}`;
    case "fill":
      return `Fill ${action.selector}`;
    case "press":
      return `Press ${action.key} in ${action.selector}`;
    case "wait_for":
      return `Wait for ${action.selector}`;
    case "expect_text":
      return `Expect ${action.selector} to contain ${action.value}`;
    case "expect_visible":
      return `Expect ${action.selector} to be visible`;
    case "expect_url":
      return `Expect URL ${action.value}`;
  }
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
