export function uploadWasRejected(
  status: number,
  code?: string,
  replaying = false,
) {
  return (
    (status >= 400 && status < 500) ||
    (!replaying && status === 503 && code === "MEDIA_PROBE_UNAVAILABLE")
  );
}
