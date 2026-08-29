import assert from "node:assert/strict";
import test from "node:test";
import { shouldShowAnalyzeBug } from "./analysis-availability.mjs";

test("Analyze Bug is available for a pending investigation with zero evidence", () => {
  const investigation = {
    status: "pending",
    evidence: [],
  };

  assert.equal(
    shouldShowAnalyzeBug(investigation.status, false),
    true,
  );
});

test("Analyze Bug is hidden while analysis is running", () => {
  assert.equal(shouldShowAnalyzeBug("pending", true), false);
  assert.equal(shouldShowAnalyzeBug("running", false), false);
});
