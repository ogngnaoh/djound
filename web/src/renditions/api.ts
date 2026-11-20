class RenditionError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}
export async function requestRendition(path = "", options?: RequestInit) {
  const res = await fetch("/api/renditions" + path, options);
  if (!res.ok) {
    const details = await res.json().catch(() => ({}));
    throw new RenditionError(
      res.status,
      details.detail || details.message || `Request failed (${res.status}).`,
      details.code,
    );
  }
  return res.status === 204 ? undefined : res.json();
}
