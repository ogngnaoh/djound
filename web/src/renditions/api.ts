import type { components } from "../api-schema";

export type Recording = components["schemas"]["RenditionIndex"];
export type Rendition = components["schemas"]["RenditionTake"];
export type RecordingProject = components["schemas"]["RenditionProject"];
export type RenditionCommand = components["schemas"]["RenditionCommand"];
export type RenditionAnswer = components["schemas"]["RenditionAnswer"];
export class RenditionError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function renditionRequest(path: string, options?: RequestInit) {
  const res = await fetch("/api/renditions" + path, options);
  if (!res.ok) {
    const details = await res.json().catch(() => ({}));
    throw new RenditionError(
      res.status,
      details.detail || details.message || `Request failed (${res.status}).`,
    );
  }
  return res;
}
export const recordingList = {
  queryKey: ["renditions"],
  queryFn: async ({ signal }: { signal: AbortSignal }): Promise<Recording[]> =>
    (await renditionRequest("", { signal })).json(),
};
export const projectKey = (id: string) => ["rendition-project", id];
export const readProject = async (
  id: string,
  signal?: AbortSignal,
): Promise<RecordingProject> =>
  (await renditionRequest("/" + id, { signal })).json();
