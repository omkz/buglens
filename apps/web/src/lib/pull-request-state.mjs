export function canCreatePullRequest(agentRun, requestActive = false) {
  return Boolean(
    agentRun?.status === "completed" &&
      agentRun.result?.fix_proposal?.status === "proposed" &&
      agentRun.pull_request_status !== "created" &&
      agentRun.pull_request_status !== "creating" &&
      !requestActive,
  );
}

export function reconciledCreatedPullRequest(agentRun, expectedAttemptId) {
  if (
    agentRun?.attempt_id !== expectedAttemptId ||
    agentRun.pull_request_status !== "created" ||
    !agentRun.pull_request
  ) {
    return null;
  }
  return agentRun;
}
