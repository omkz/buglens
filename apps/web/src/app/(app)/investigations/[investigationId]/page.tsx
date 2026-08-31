"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import { AsyncActivity } from "../../_components/async-activity";
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

const investigationSteps = [
  { stage: "starting", label: "Starting investigation" },
  { stage: "investigating_repository", label: "Investigating repository" },
  { stage: "searching_duplicates", label: "Searching duplicate issues" },
  { stage: "preparing_reproduction", label: "Preparing reproduction" },
  { stage: "running_browser", label: "Running browser reproduction" },
] as const;

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
  const [isValidatingFix, setIsValidatingFix] = useState(false);
  const [fixValidationError, setFixValidationError] = useState<string | null>(null);
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
    if (
      latestAgentRunAttemptRef.current !== expectedAttemptId ||
      body.attempt_id !== expectedAttemptId
    ) {
      return null;
    }
    setState((current) =>
      current.status === "ready"
        ? { ...current, agentRun: body }
        : current,
    );
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
    let attemptSettled = false;
    let postFinished = false;

    const closeProgressSource = () => {
      if (progressSourceRef.current === progressSource) {
        progressSourceRef.current = null;
      }
      progressSource.close();
    };

    const settleAttempt = (
      agentRun: AgentRunStatus,
      failureMessage = "Autonomous investigation failed. Please try again.",
    ) => {
      if (
        attemptSettled ||
        latestAgentRunAttemptRef.current !== attemptId ||
        agentRun.attempt_id !== attemptId ||
        (agentRun.status !== "completed" && agentRun.status !== "failed")
      ) {
        return false;
      }
      attemptSettled = true;
      setState((current) =>
        current.status === "ready"
          ? { ...current, agentRun }
          : current,
      );
      setInvestigationError(
        agentRun.status === "completed" ? null : failureMessage,
      );
      setLiveProgressDisconnected(false);
      closeProgressSource();
      investigationRequestActiveRef.current = false;
      setIsInvestigating(false);
      return true;
    };

    const failAttempt = (error: unknown) => {
      if (
        attemptSettled ||
        latestAgentRunAttemptRef.current !== attemptId
      ) {
        return;
      }
      attemptSettled = true;
      closeProgressSource();
      investigationRequestActiveRef.current = false;
      setIsInvestigating(false);
      setInvestigationError(
        error instanceof Error
          ? error.message
          : "Autonomous investigation failed. Please try again.",
      );
    };

    const receiveProgress = (event: MessageEvent<string>) => {
      try {
        const progress = JSON.parse(event.data) as Partial<
          AgentRunProgress & { attempt_id: string }
        >;
        if (
          attemptSettled ||
          progress.attempt_id !== attemptId ||
          latestAgentRunAttemptRef.current !== attemptId ||
          typeof progress.stage !== "string" ||
          !isAgentRunProgressStage(progress.stage) ||
          typeof progress.message !== "string"
        ) {
          return false;
        }
        setLiveProgressDisconnected(false);
        const stage = progress.stage;
        const message = progress.message;
        setState((current) => {
          if (current.status !== "ready") return current;
          const currentAttempt = current.agentRun.attempt_id === attemptId;
          return {
            ...current,
            agentRun: {
              investigation_id: current.agentRun.investigation_id,
              attempt_id: attemptId,
              status: "running",
              result: currentAttempt ? current.agentRun.result : null,
              progress: {
                stage,
                message,
                updated_at: new Date().toISOString(),
              },
              github_issue_status: currentAttempt
                ? current.agentRun.github_issue_status
                : null,
              github_issue: currentAttempt ? current.agentRun.github_issue : null,
              fix_validation: currentAttempt
                ? current.agentRun.fix_validation
                : null,
            },
          };
        });
        return true;
      } catch {
        // Ignore malformed transport data; the POST remains authoritative.
        return false;
      }
    };
    progressSource.addEventListener("progress", receiveProgress);
    const reconcileTerminalEvent = async (event: Event) => {
      const accepted = receiveProgress(event as MessageEvent<string>);
      if (!accepted) return;
      closeProgressSource();
      try {
        const persisted = await refreshAgentRunStatus(attemptId);
        if (persisted) settleAttempt(persisted);
      } catch {
        if (postFinished && !attemptSettled) {
          setLiveProgressDisconnected(true);
        }
      }
    };
    progressSource.addEventListener("complete", reconcileTerminalEvent);
    progressSource.addEventListener("failed", reconcileTerminalEvent);
    progressSource.onerror = () => {
      if (
        progressSourceRef.current === progressSource &&
        !attemptSettled
      ) {
        // Keep the native reconnect behavior so a transient disconnect cannot
        // discard the persisted terminal event.
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
      if (!settleAttempt(body)) {
        throw new Error("Autonomous investigation returned an invalid state.");
      }
    } catch (error) {
      if (attemptSettled) return;
      try {
        const reconciled = await refreshAgentRunStatus(attemptId);
        if (
          reconciled &&
          settleAttempt(
            reconciled,
            error instanceof Error ? error.message : undefined,
          )
        ) {
          return;
        }
        if (reconciled?.status === "running") {
          // The backend still owns this attempt; its terminal SSE event will
          // reconcile the persisted result even though the POST transport ended.
          return;
        }
      } catch {
        // The request error below remains authoritative when no run can be found.
      }
      failAttempt(error);
    } finally {
      postFinished = true;
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

  async function validateFix() {
    if (state.status !== "ready" || isValidatingFix) return;
    setIsValidatingFix(true);
    setFixValidationError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/fix-validation`,
        { method: "POST", credentials: "include" },
      );
      const body = (await response.json().catch(() => null)) as
        | AgentRunStatus
        | { detail?: string }
        | null;
      if (!response.ok || !body || !("status" in body)) {
        throw new Error(
          body && "detail" in body && body.detail
            ? body.detail
            : "Fix validation failed. Please try again.",
        );
      }
      setState((current) =>
        current.status === "ready" ? { ...current, agentRun: body } : current,
      );
    } catch (error) {
      setFixValidationError(
        error instanceof Error
          ? error.message
          : "Fix validation failed. Please try again.",
      );
    } finally {
      setIsValidatingFix(false);
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
        <InvestigationDetailSkeleton />
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
              <div
                className="flex max-w-xl flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4 text-sm text-zinc-300"
              >
                <AsyncActivity label="Analyzing report and evidence…" />
                <div
                  role="progressbar"
                  aria-label="Bug analysis in progress"
                  className="h-1 overflow-hidden rounded-full bg-zinc-800"
                >
                  <div
                    aria-hidden="true"
                    className="h-full w-2/5 animate-pulse rounded-full bg-zinc-400/70 motion-reduce:animate-none"
                  />
                </div>
              </div>
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

              {(state.agentRun.status !== null || isInvestigating) && (
                <InvestigationProgress
                  stage={
                    state.agentRun.progress?.stage ??
                    (state.agentRun.status === "completed"
                      ? "completed"
                      : state.agentRun.status === "failed"
                        ? "failed"
                        : undefined)
                  }
                  message={state.agentRun.progress?.message}
                  disconnected={liveProgressDisconnected && isInvestigating}
                />
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
                  fixValidation={state.agentRun.fix_validation}
                  isValidatingFix={isValidatingFix}
                  fixValidationError={fixValidationError}
                  onValidateFix={validateFix}
                />
              )}
            </section>
          )}
        </article>
      )}
    </div>
  );
}

function InvestigationDetailSkeleton() {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-8">
      <span className="sr-only">Loading investigation…</span>
      <div
        aria-hidden="true"
        className="flex animate-pulse flex-col gap-8 motion-reduce:animate-none"
      >
        <div className="flex flex-col gap-3">
          <div className="h-7 w-3/5 rounded bg-zinc-800" />
          <div className="h-4 w-2/5 rounded bg-zinc-900" />
          <div className="h-3 w-1/4 rounded bg-zinc-900" />
        </div>
        <div className="h-28 rounded-lg border border-zinc-800 bg-zinc-950/50" />
        <div className="flex flex-col gap-3">
          <div className="h-5 w-24 rounded bg-zinc-800" />
          <div className="h-16 rounded-lg border border-zinc-800 bg-zinc-950/50" />
        </div>
        <div className="flex flex-col gap-3 border-t border-zinc-800 pt-8">
          <div className="h-5 w-32 rounded bg-zinc-800" />
          <div className="h-20 rounded-lg border border-zinc-800 bg-zinc-950/50" />
        </div>
      </div>
    </div>
  );
}

function InvestigationProgress({
  stage,
  message,
  disconnected,
}: {
  stage: AgentRunProgress["stage"] | undefined;
  message: string | undefined;
  disconnected: boolean;
}) {
  const activeIndex = investigationSteps.findIndex(
    (step) => step.stage === stage,
  );
  const completed = stage === "completed";
  const failed = stage === "failed";
  const progressMessage =
    message ||
    (completed
      ? "Investigation completed."
      : failed
        ? "Investigation failed."
        : stage
          ? progressStageLabel(stage)
          : "Waiting for live progress…");

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        Investigation progress
      </p>
      <ol className="grid gap-2 sm:grid-cols-2">
        {investigationSteps.map((step, index) => {
          const stepCompleted = completed || (activeIndex >= 0 && index < activeIndex);
          const active = index === activeIndex;
          return (
            <li
              key={step.stage}
              aria-current={active ? "step" : undefined}
              className={`flex items-center gap-2 text-xs ${
                stepCompleted
                  ? "text-emerald-300"
                  : active
                    ? "text-zinc-100"
                    : "text-zinc-600"
              }`}
            >
              {stepCompleted ? (
                <span
                  aria-hidden="true"
                  className="flex size-4 shrink-0 items-center justify-center rounded-full bg-emerald-950 text-[10px]"
                >
                  ✓
                </span>
              ) : active ? (
                <span
                  aria-hidden="true"
                  className="size-4 shrink-0 animate-pulse rounded-full border border-zinc-400 bg-zinc-600/50 motion-reduce:animate-none"
                />
              ) : (
                <span
                  aria-hidden="true"
                  className="size-4 shrink-0 rounded-full border border-zinc-800"
                />
              )}
              <span>
                <span className="sr-only">
                  {stepCompleted
                    ? "Completed: "
                    : active
                      ? "Current: "
                      : "Upcoming: "}
                </span>
                {step.label}
              </span>
            </li>
          );
        })}
        <li
          className={`flex items-center gap-2 text-xs ${
            completed
              ? "text-emerald-300"
              : failed
                ? "text-red-300"
                : "text-zinc-600"
          }`}
        >
          <span
            aria-hidden="true"
            className={`flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] ${
              completed
                ? "bg-emerald-950"
                : failed
                  ? "bg-red-950"
                  : "border border-zinc-800"
            }`}
          >
            {completed ? "✓" : failed ? "×" : ""}
          </span>
          <span>
            <span className="sr-only">
              {completed ? "Completed: " : failed ? "Failed: " : "Upcoming: "}
            </span>
            {failed ? "Investigation failed" : "Investigation completed"}
          </span>
        </li>
      </ol>
      {completed || failed ? (
        <p aria-live="polite" className="text-sm text-zinc-300">
          {progressMessage}
        </p>
      ) : (
        <span className="text-sm text-zinc-300">
          <AsyncActivity label={progressMessage} />
        </span>
      )}
      {disconnected && (
        <p className="text-xs text-amber-500/80">
          Live progress disconnected. The investigation is still running.
        </p>
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
  fixValidation,
  isValidatingFix,
  fixValidationError,
  onValidateFix,
}: {
  result: AgentRunResult;
  githubIssueStatus: "creating" | "created" | "failed" | null;
  githubIssue: GitHubIssue | null;
  isCreatingIssue: boolean;
  issueError: string | null;
  onCreateIssue: () => void;
  fixValidation: AgentRunStatus["fix_validation"];
  isValidatingFix: boolean;
  fixValidationError: string | null;
  onValidateFix: () => void;
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

      <ProposedFix
        proposal={result.fix_proposal}
        validation={fixValidation}
        isValidating={isValidatingFix}
        error={fixValidationError}
        onValidate={onValidateFix}
      />

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
          <p className="text-sm text-zinc-300">
            <AsyncActivity label="Creating GitHub issue…" />
          </p>
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

function ProposedFix({
  proposal,
  validation,
  isValidating,
  error,
  onValidate,
}: {
  proposal: AgentRunResult["fix_proposal"];
  validation: AgentRunStatus["fix_validation"];
  isValidating: boolean;
  error: string | null;
  onValidate: () => void;
}) {
  if (proposal.status === "not_proposed") {
    return (
      <div className="flex flex-col gap-2 border-t border-zinc-800 pt-6">
        <h3 className="text-sm font-medium text-zinc-200">Proposed fix</h3>
        <p className="text-sm leading-6 text-zinc-500">
          Buglensa could not confidently propose a safe fix.
          {proposal.reason ? ` ${proposal.reason}` : ""}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 border-t border-zinc-800 pt-6">
      <div className="space-y-2">
        <h3 className="text-sm font-medium text-zinc-200">Proposed fix</h3>
        <p className="text-sm leading-6 text-zinc-400">{proposal.summary}</p>
      </div>
      {proposal.files.map((file) => (
        <div
          key={file.path}
          className="overflow-hidden rounded-md border border-zinc-800 bg-zinc-950"
        >
          <div className="border-b border-zinc-800 px-4 py-3">
            <code className="text-sm text-zinc-200">{file.path}</code>
            <p className="mt-1 text-xs leading-5 text-zinc-500">
              {file.explanation}
            </p>
          </div>
          <pre
            aria-label={`Proposed changes for ${file.path}`}
            className="max-h-[32rem] overflow-auto p-4 text-xs leading-5"
          >
            <code>
              {file.diff.split("\n").map((line, index) => (
                <span
                  key={`${index}-${line}`}
                  className={`block min-w-max ${diffLineClass(line)}`}
                >
                  {line || " "}
                </span>
              ))}
            </code>
          </pre>
        </div>
      ))}
      <div className="space-y-3 border-t border-zinc-800 pt-4">
        <button
          type="button"
          onClick={onValidate}
          disabled={isValidating || validation?.status === "running"}
          className="rounded-full border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-200 hover:border-zinc-500 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isValidating || validation?.status === "running" ? (
            <AsyncActivity label="Validating fix…" />
          ) : (
            "Validate Fix"
          )}
        </button>
        {error && <p className="text-sm text-red-400">{error}</p>}
        {validation && validation.status !== "running" && (
          <div className="space-y-3 rounded-md bg-zinc-900/60 p-4 text-sm">
            <p className="font-medium capitalize text-zinc-200">
              {validation.status.replaceAll("_", " ")}
            </p>
            <p className="text-zinc-400">{validation.summary}</p>
            <ul className="space-y-2 text-zinc-400">
              {validation.checks.map((check) => (
                <li key={check.name}>
                  <span className={check.status === "passed" ? "text-emerald-300" : "text-red-300"}>
                    {check.status === "passed" ? "Pass" : "Fail"}
                  </span>{" "}
                  {check.name}
                  {check.output && <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-xs text-zinc-600">{check.output}</pre>}
                </li>
              ))}
            </ul>
            <p className="text-xs text-zinc-500">
              Before: {validation.reproduction_before ?? "Not available"} · After: {validation.reproduction_after ?? "Not run"}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function diffLineClass(line: string) {
  if (line.startsWith("@@")) return "text-sky-400";
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "text-zinc-500";
  }
  if (line.startsWith("+")) return "bg-emerald-950/40 text-emerald-300";
  if (line.startsWith("-")) return "bg-red-950/40 text-red-300";
  return "text-zinc-500";
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
