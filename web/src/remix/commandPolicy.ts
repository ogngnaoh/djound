// These typed responses guarantee the server did not admit a creation attempt.
// An unclassified 5xx or transport error still has an uncertain outcome.
export function isDefiniteRejection(status: number, code?: string) {
  return (
    (status >= 400 && status < 500) ||
    (status === 503 &&
      (code === "PROVIDER_NOT_CONFIGURED" || code === "CAPACITY_UNAVAILABLE"))
  );
}
