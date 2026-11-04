// These typed responses guarantee the server did not admit a creation attempt.
// An unclassified 5xx or transport error still has an uncertain outcome.
export function isDefiniteRejection(status: number, code?: string) {
  return (
    (status >= 400 && status < 500) ||
    (status === 503 &&
      (code === "PROVIDER_NOT_CONFIGURED" || code === "CAPACITY_UNAVAILABLE"))
  );
}

// Only call at a deliberate admission click. Pending replay never uses live UI state.
export function captureAcknowledgement(attemptId?: string) {
  return attemptId ? { acknowledgedUnknownAttemptId: attemptId } : {};
}
