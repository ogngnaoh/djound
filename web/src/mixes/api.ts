import type { components } from "../api-schema";
export type Selection = components["schemas"]["MixSelection"];
export type Take = components["schemas"]["MixTake"];
export type Mix = components["schemas"]["MixState"];
export type Reply = components["schemas"]["MixEditReply"];
export class MixError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function mixRequest<T>(
  path: string,
  method = "GET",
  payload?: object,
  key?: string,
): Promise<T> {
  const form = payload instanceof FormData;
  const headers: Record<string, string> = {};
  if (payload && !form) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetch("/api/mixes" + path, {
    method,
    headers,
    body: form ? payload : payload ? JSON.stringify(payload) : undefined,
  });
  if (!res.ok) {
    const details = await res.json().catch(() => ({}));
    throw new MixError(
      res.status,
      details.detail || "Could not complete the request. Try again.",
    );
  }
  return res.json();
}
export const formatTime = (v: number) => {
  const seconds = Math.max(0, v) || 0;
  return (
    Math.floor(seconds / 60) +
    ":" +
    String(Math.floor(seconds % 60)).padStart(2, "0") +
    "." +
    Math.floor((seconds * 10) % 10)
  );
};
export const level = (v: number) => (v > 0 ? "+" : "") + v + " dB";
