/**
 * @param {"pending" | "running" | "completed" | "failed"} investigationStatus
 * @param {boolean} isAnalyzing
 * @returns {boolean}
 */
export function shouldShowAnalyzeBug(investigationStatus, isAnalyzing) {
  return investigationStatus === "pending" && !isAnalyzing;
}
