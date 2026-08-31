import assert from "node:assert/strict";
import test from "node:test";

import {
  canCreatePullRequest,
  reconciledCreatedPullRequest,
} from "./pull-request-state.mjs";

function completedRun(validation = null) {
  return {
    attempt_id: "current-attempt",
    status: "completed",
    result: { fix_proposal: { status: "proposed" } },
    fix_validation: validation,
    pull_request_status: null,
    pull_request: null,
  };
}

test("fix validation never gates explicit pull request creation", () => {
  for (const status of [
    null,
    "validated",
    "validation_failed",
    "blocked",
    "stale_proposal",
    "not_run",
  ]) {
    const validation = status ? { status } : null;
    assert.equal(canCreatePullRequest(completedRun(validation)), true);
  }
});

test("persisted created PR restores after refresh and ambiguous POST failure", () => {
  const run = {
    ...completedRun(),
    pull_request_status: "created",
    pull_request: {
      number: 42,
      title: "Fix: Checkout navigation",
      url: "https://github.com/omkz/buglensa/pull/42",
      branch: "buglensa/fix-123456789abc",
    },
  };
  assert.equal(
    reconciledCreatedPullRequest(run, "current-attempt"),
    run,
  );
  assert.equal(reconciledCreatedPullRequest(run, "stale-attempt"), null);
  assert.equal(canCreatePullRequest(run), false);
});

test("in-progress publication and duplicate clicks are guarded", () => {
  assert.equal(
    canCreatePullRequest({ ...completedRun(), pull_request_status: "creating" }),
    false,
  );
  assert.equal(canCreatePullRequest(completedRun(), true), false);
});
